"""verdia — jogo da grama (bancada do NEXT).

O computador comanda a partida: sorteia a sequência, pede ao Arduino para
acender os LEDs, usa a câmera para ver em qual grama a mão foi colocada e
mostra o placar e o ranking.
"""

from collections import deque
import json
import os
import random
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import cv2
import numpy as np
import pygame
import serial
import serial.tools.list_ports


# Variáveis de ambiente (explicadas no README)
CAMERA = int(os.environ.get("VERDIA_CAMERA", "0"))
PORTA_ARDUINO = os.environ.get("VERDIA_PORTA")

# Ritmo do jogo, em segundos
TEMPO_LED_ACESO = 0.6
TEMPO_LED_APAGADO = 0.3
TEMPO_ANTES_SEQUENCIA = 1.0
TEMPO_CONFIRMA = 0.4      # mão parada sobre uma grama por esse tempo vira jogada
TEMPO_MAO_FORA = 0.2      # todas as gramas livres por esse tempo = mão saiu
TEMPO_LIMITE = 8.0        # sem jogada por esse tempo = fim de jogo
TEMPO_INICIO = 2.5        # a animação de início do Arduino dura cerca de 2 s
TEMPO_FIM = 8.0

# Detecção: fração da região que mudou em relação à imagem de referência
LIMIAR_MAO = 0.20
LIMIAR_LIVRE = 0.10
DIFERENCA_MIN = 40        # quanto um pixel precisa mudar (0 a 255) para contar como mão
ATUALIZA_REF = 0.03       # a cada frame, a referência se aproxima 3% do que a câmera vê livre

NOMES_GRAMAS = ["pequena", "média", "grande"]
COMANDOS_LED = [b"v", b"a", b"r"]

# Cores da marca
BRANCO = (255, 255, 255)
TEAL = (46, 140, 128)
TEAL_ESCURO = (22, 84, 76)
VERDE_MARCA = (112, 180, 114)
VERDE_CLARO = (139, 207, 120)
MENTA = (197, 226, 208)
LIMA = (233, 255, 60)
CORES_LED = [(72, 214, 110), (255, 214, 64), (255, 94, 84)]

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQ_CALIBRACAO = os.path.join(PASTA, "calibracao.json")
ARQ_REFERENCIA = os.path.join(PASTA, "referencia.png")
ARQ_RANKING = os.path.join(PASTA, "ranking.json")
ARQ_FONTE_MARCA = os.path.join(PASTA, "Alice-Regular.ttf")


def carregar_json(arquivo, padrao):
    try:
        with open(arquivo, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def salvar_json(arquivo, dados):
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def conectar_arduino():
    porta = PORTA_ARDUINO

    if porta is None:
        for p in serial.tools.list_ports.comports():
            if "usbmodem" in p.device or "usbserial" in p.device:
                porta = p.device
                break

    if porta is None:
        print("Arduino não encontrado. O jogo vai rodar sem os LEDs.")
        return None

    conexao = serial.Serial(porta, 9600, timeout=0)
    time.sleep(2)  # o UNO reinicia quando a porta serial é aberta
    print("Arduino conectado em", porta)
    return conexao


def abrir_camera():
    camera = cv2.VideoCapture(CAMERA)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not camera.isOpened():
        print(f"Câmera {CAMERA} não abriu. Tentando de novo em alguns segundos.")
    return camera


def suavizar(frame):
    return cv2.GaussianBlur(frame, (9, 9), 0).astype(np.float32)


def recortes(imagem, zonas):
    return [imagem[y:y + h, x:x + w] for x, y, w, h in zonas]


def fundo_da_marca(largura, altura):
    # Degradê no estilo das artes da verdia: manchas desfocadas sobre o teal
    base = np.zeros((90, 160, 3), np.uint8)
    base[:] = TEAL

    manchas = [
        ((80, 98), (95, 22), VERDE_MARCA),
        ((-5, 55), (22, 40), VERDE_CLARO),
        ((128, -6), (34, 10), LIMA),
        ((168, 40), (18, 30), MENTA),
        ((0, 95), (40, 12), MENTA),
    ]
    for centro, eixos, cor in manchas:
        cv2.ellipse(base, centro, eixos, -12, 0, 360, cor, -1)

    base = cv2.GaussianBlur(base, (0, 0), 14)
    base = cv2.resize(base, (largura, altura), interpolation=cv2.INTER_CUBIC)
    return pygame.image.frombuffer(base.tobytes(), (largura, altura), "RGB").copy()


def tela_para_camera(ponto, vista, largura, altura):
    x0, y0, escala = vista
    x = int((ponto[0] - x0) / escala)
    y = int((ponto[1] - y0) / escala)
    return min(max(x, 0), largura), min(max(y, 0), altura)


class Jogo:

    def __init__(self):
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption("verdia")
        self.tela = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        self.tela_cheia = False
        self.relogio = pygame.time.Clock()
        self.preparar_tela()

        self.camera = abrir_camera()
        self.t_frame = time.monotonic()
        self.frame = None
        self.vista = None
        self.cobertura = [0.0, 0.0, 0.0]
        self.historico = deque(maxlen=3)

        self.arduino = conectar_arduino()
        self.led_aceso = None

        self.calib = carregar_json(ARQ_CALIBRACAO, None)
        imagem = cv2.imread(ARQ_REFERENCIA) if os.path.exists(ARQ_REFERENCIA) else None
        if self.calib and imagem is not None and list(imagem.shape[1::-1]) == self.calib["tamanho"]:
            self.usar_referencia(imagem, self.calib["zonas"])
        else:
            self.calib = None
        self.ranking = carregar_json(ARQ_RANKING, [])

        self.estado = "ESPERA"
        self.rodando = True
        self.agora = time.monotonic()
        self.aviso = ""

        self.nome = ""
        self.sequencia = []
        self.posicao = 0
        self.pontos = 0
        self.motivo = ""
        self.ultima_entrada = None
        self.t_evento = 0.0
        self.t_ultima = 0.0
        self.t_livre = None
        self.zona_atual = None
        self.t_zona = 0.0
        self.esperando_soltar = False

        self.zonas_novas = []
        self.arraste = None

        self.enviar(b"I")

    # ----- Tela -----

    def preparar_tela(self):
        largura, altura = self.tela.get_size()
        k = altura / 720

        if os.path.exists(ARQ_FONTE_MARCA):
            marca = lambda tamanho: pygame.font.Font(ARQ_FONTE_MARCA, int(tamanho * k))
        else:
            marca = lambda tamanho: pygame.font.SysFont("georgia", int(tamanho * k))

        texto = "helveticaneue,helvetica,arial"
        self.fonte_logo = marca(60)
        self.fonte_titulo = marca(68)
        self.fonte_numero = marca(64)
        self.fonte_media = pygame.font.SysFont(texto, int(34 * k))
        self.fonte_pequena = pygame.font.SysFont(texto, int(22 * k))
        self.k = k
        self.fundo = fundo_da_marca(largura, altura)

    def alternar_tela_cheia(self):
        self.tela_cheia = not self.tela_cheia
        if self.tela_cheia:
            self.tela = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.tela = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        self.preparar_tela()

    # ----- LEDs -----

    def enviar(self, comando):
        if self.arduino is None:
            return
        try:
            self.arduino.write(comando)
        except serial.SerialException:
            print("Conexão com o Arduino perdida. Seguindo sem os LEDs.")
            self.arduino = None

    def acender(self, cor):
        self.led_aceso = cor
        self.enviar(COMANDOS_LED[cor])

    def apagar(self):
        self.led_aceso = None
        self.enviar(b"0")

    # ----- Câmera -----

    def medir(self):
        frame = self.frame
        if frame is None or self.calib is None:
            self.cobertura = [0.0, 0.0, 0.0]
            return

        altura, largura = frame.shape[:2]
        if [largura, altura] != self.calib["tamanho"]:
            self.calib = None
            self.aviso = "A resolução da câmera mudou. Calibre de novo (C)."
            return

        atual = recortes(suavizar(frame), self.calib["zonas"])

        # Quanto a luz mudou em cada região desde a referência (um valor por cor).
        # A mediana das três ignora a região que está com a mão.
        razao = [np.median(a / np.maximum(r, 1), axis=(0, 1)) for a, r in zip(atual, self.ref_zonas)]
        luz = np.median(np.array(razao) / self.fatores, axis=0)

        medidas = []
        for i in range(3):
            corrigida = atual[i] / (luz * self.fatores[i])
            mudou = np.abs(corrigida - self.ref_zonas[i]).max(axis=2) > DIFERENCA_MIN
            medidas.append(float(mudou.mean()))

            # Região livre: aprende como a luz dela difere da média (luz que muda de lugar)
            if medidas[i] < 0.3:
                self.fatores[i] = 0.7 * self.fatores[i] + 0.3 * razao[i] / luz

            # Região livre: a referência acompanha a imagem, menos nos pixels que parecem mão
            if medidas[i] < LIMIAR_LIVRE:
                ref = self.ref_zonas[i]
                ref += ATUALIZA_REF * (corrigida - ref) * ~mudou[..., None]

        self.historico.append(medidas)
        self.cobertura = list(np.median(self.historico, axis=0))

    def usar_referencia(self, imagem, zonas):
        self.ref_zonas = recortes(suavizar(imagem), zonas)
        self.fatores = np.ones((3, 3), np.float32)
        self.historico.clear()

    # ----- Partida -----

    def voltar_espera(self):
        self.arraste = None
        pygame.key.stop_text_input()
        self.estado = "ESPERA"
        self.led_aceso = None
        self.enviar(b"I")

    def pedir_nome(self):
        self.aviso = ""
        self.nome = ""
        self.estado = "NOME"
        self.enviar(b"I")
        pygame.key.start_text_input()

    def iniciar_partida(self):
        pygame.key.stop_text_input()
        self.sequencia = []
        self.pontos = 0
        self.led_aceso = None
        self.enviar(b"S")
        self.estado = "INICIO"
        self.t_evento = self.agora + TEMPO_INICIO

    def nova_rodada(self):
        self.sequencia.append(random.randrange(3))
        self.posicao = 0
        self.apagar()
        self.estado = "MOSTRANDO"
        self.t_evento = self.agora + TEMPO_ANTES_SEQUENCIA

    def vez_do_jogador(self):
        self.estado = "JOGADOR"
        self.posicao = 0
        self.esperando_soltar = True  # quem pôs a mão antes da hora precisa tirar
        self.t_livre = None
        self.zona_atual = None
        self.t_ultima = self.agora

    def mao_fora(self):
        if max(self.cobertura) < LIMIAR_LIVRE:
            if self.t_livre is None:
                self.t_livre = self.agora
            return self.agora - self.t_livre >= TEMPO_MAO_FORA

        self.t_livre = None
        return False

    def ler_jogada(self):
        if self.agora - self.t_ultima > TEMPO_LIMITE:
            self.fim_de_jogo("O tempo acabou")
            return

        if self.esperando_soltar:
            if self.mao_fora():
                self.esperando_soltar = False
                self.apagar()
            return

        # Só conta quando exatamente uma grama está coberta
        cobertas = [i for i in range(3) if self.cobertura[i] >= LIMIAR_MAO]
        if len(cobertas) != 1:
            self.zona_atual = None
            return

        zona = cobertas[0]
        if zona != self.zona_atual:
            self.zona_atual = zona
            self.t_zona = self.agora
        elif self.agora - self.t_zona >= TEMPO_CONFIRMA:
            self.registrar(zona)

    def registrar(self, zona):
        self.zona_atual = None
        self.t_ultima = self.agora

        certa = self.sequencia[self.posicao]
        if zona != certa:
            self.fim_de_jogo(f"Era a grama {NOMES_GRAMAS[certa]}")
            return

        self.acender(zona)
        self.posicao += 1
        self.t_livre = None

        if self.posicao == len(self.sequencia):
            self.pontos = len(self.sequencia)
            self.estado = "ACERTOU"
            self.t_evento = self.agora + 1.0
        else:
            self.esperando_soltar = True

    def fim_de_jogo(self, motivo):
        self.enviar(b"X")
        self.led_aceso = None
        self.motivo = motivo
        self.estado = "FIM"
        self.t_evento = self.agora + TEMPO_FIM

        entrada = {
            "nome": self.nome.strip(),
            "pontos": self.pontos,
            "hora": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.ranking.append(entrada)
        salvar_json(ARQ_RANKING, self.ranking)
        self.ultima_entrada = entrada

    def atualizar(self):
        if self.estado == "INICIO":
            if self.agora >= self.t_evento:
                self.nova_rodada()

        elif self.estado == "MOSTRANDO":
            if self.agora < self.t_evento:
                return
            if self.led_aceso is not None:
                self.apagar()
                self.posicao += 1
                self.t_evento = self.agora + TEMPO_LED_APAGADO
            elif self.posicao < len(self.sequencia):
                self.acender(self.sequencia[self.posicao])
                self.t_evento = self.agora + TEMPO_LED_ACESO
            else:
                self.vez_do_jogador()

        elif self.estado == "JOGADOR":
            self.ler_jogada()

        elif self.estado == "ACERTOU":
            if self.mao_fora():
                if self.led_aceso is not None:
                    self.apagar()
                if self.agora >= self.t_evento:
                    self.nova_rodada()

        elif self.estado == "FIM":
            if self.agora >= self.t_evento:
                self.voltar_espera()

    # ----- Teclado e mouse -----

    def tratar_eventos(self):
        for evento in pygame.event.get():
            if evento.type == pygame.QUIT:
                self.rodando = False

            elif evento.type == pygame.VIDEORESIZE:
                self.tela = pygame.display.get_surface()
                self.preparar_tela()

            elif evento.type == pygame.TEXTINPUT and self.estado == "NOME":
                if len(self.nome) < 20:
                    self.nome += evento.text

            elif evento.type == pygame.KEYDOWN:
                self.tecla(evento.key)

            elif self.estado == "CALIBRANDO":
                self.mouse_calibracao(evento)

    def tecla(self, k):
        enter = k in (pygame.K_RETURN, pygame.K_KP_ENTER)

        if k == pygame.K_ESCAPE:
            self.voltar_espera()

        elif self.estado == "NOME":
            if k == pygame.K_BACKSPACE:
                self.nome = self.nome[:-1]
            elif enter and self.nome.strip():
                self.iniciar_partida()

        elif k == pygame.K_q:
            self.rodando = False

        elif k == pygame.K_f:
            self.alternar_tela_cheia()

        elif self.estado == "RANKING":
            if k == pygame.K_r:
                self.voltar_espera()

        elif k == pygame.K_r and self.estado in ("ESPERA", "FIM"):
            self.estado = "RANKING"

        elif self.estado == "ESPERA":
            if enter and self.calib:
                self.pedir_nome()
            elif k == pygame.K_c:
                self.aviso = ""
                self.zonas_novas = []
                self.arraste = None
                self.estado = "CALIBRANDO"
            elif k == pygame.K_b:
                self.recalibrar_referencia()

        elif self.estado == "FIM" and enter:
            self.pedir_nome()

    def recalibrar_referencia(self):
        frame = self.frame
        if frame is None or self.calib is None:
            return

        cv2.imwrite(ARQ_REFERENCIA, frame)
        self.usar_referencia(frame, self.calib["zonas"])
        self.aviso = "Referência atualizada."

    def mouse_calibracao(self, evento):
        frame = self.frame
        vista = self.vista
        if frame is None or vista is None:
            return

        if evento.type == pygame.MOUSEBUTTONDOWN and evento.button in (1, 3):
            self.arraste = [evento.pos, evento.pos]

        elif evento.type == pygame.MOUSEMOTION and self.arraste:
            self.arraste[1] = evento.pos

        elif evento.type == pygame.MOUSEBUTTONUP and evento.button in (1, 3) and self.arraste:
            altura, largura = frame.shape[:2]
            x1, y1 = tela_para_camera(self.arraste[0], vista, largura, altura)
            x2, y2 = tela_para_camera(evento.pos, vista, largura, altura)
            self.arraste = None

            zona = [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]
            if zona[2] < 10 or zona[3] < 10:
                return

            self.aviso = ""
            self.zonas_novas.append(zona)
            if len(self.zonas_novas) < 3:
                return

            self.calib = {"tamanho": [largura, altura], "zonas": self.zonas_novas}
            salvar_json(ARQ_CALIBRACAO, self.calib)
            self.recalibrar_referencia()
            self.aviso = "Câmera calibrada."
            self.voltar_espera()

    # ----- Desenho -----

    def texto(self, fonte, conteudo, cor=BRANCO, centro=None, pos=None, alfa=255):
        img = fonte.render(conteudo, True, cor)
        img.set_alpha(alfa)
        rect = img.get_rect(center=centro) if centro else img.get_rect(topleft=pos)
        self.tela.blit(img, rect)

    def cartao(self, rect, alfa=110):
        superficie = pygame.Surface(rect.size, pygame.SRCALPHA)
        raio = int(22 * self.k)
        pygame.draw.rect(superficie, (*TEAL_ESCURO, alfa), superficie.get_rect(), border_radius=raio)
        self.tela.blit(superficie, rect.topleft)

    def desenhar_camera(self, area):
        frame = self.frame
        if frame is None:
            self.vista = None
            self.texto(self.fonte_pequena, "Sem imagem da câmera", centro=area.center, alfa=180)
            return

        altura, largura = frame.shape[:2]
        escala = min(area.width / largura, area.height / altura)
        tamanho = (int(largura * escala), int(altura * escala))
        x0 = area.x + (area.width - tamanho[0]) // 2
        y0 = area.y + (area.height - tamanho[1]) // 2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = pygame.image.frombuffer(rgb.tobytes(), (largura, altura), "RGB")
        img = pygame.transform.smoothscale(img, tamanho)

        # cantos arredondados
        mascara = pygame.Surface(tamanho, pygame.SRCALPHA)
        pygame.draw.rect(mascara, (255, 255, 255, 255), mascara.get_rect(),
                         border_radius=int(14 * self.k))
        img = img.convert_alpha()
        img.blit(mascara, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)

        self.tela.blit(img, (x0, y0))
        self.vista = (x0, y0, escala)

    def desenhar_zonas(self, zonas, com_cobertura):
        vista = self.vista
        if vista is None:
            return

        x0, y0, escala = vista
        for i, (x, y, w, h) in enumerate(zonas):
            r = pygame.Rect(x0 + x * escala, y0 + y * escala, w * escala, h * escala)
            pygame.draw.rect(self.tela, CORES_LED[i], r, 2, border_radius=6)
            if com_cobertura:
                self.texto(self.fonte_pequena, f"{self.cobertura[i]:.0%}", CORES_LED[i],
                           pos=(r.x + 8, r.y + 4))

    def ranking_ordenado(self):
        return sorted(self.ranking, key=lambda r: (-r["pontos"], r["hora"]))

    def desenhar_ranking(self, area):
        self.cartao(area)
        k = self.k
        x = area.x + int(28 * k)
        y = area.y + int(22 * k)
        largura = area.width - int(56 * k)

        self.texto(self.fonte_media, "Ranking", pos=(x, y))
        y += int(56 * k)

        linha = int(34 * k)
        cabem = max(0, (area.bottom - int(16 * k) - y) // linha)
        lista = self.ranking_ordenado()[:cabem]
        if not lista:
            self.texto(self.fonte_pequena, "Ninguém jogou ainda", pos=(x, y), alfa=170)

        for i, r in enumerate(lista):
            cor = LIMA if r is self.ultima_entrada else BRANCO
            self.texto(self.fonte_pequena, f"{i + 1}", cor, pos=(x, y), alfa=170)
            self.texto(self.fonte_pequena, r["nome"], cor, pos=(x + int(36 * k), y))
            pontos = self.fonte_pequena.render(str(r["pontos"]), True, cor)
            self.tela.blit(pontos, (x + largura - pontos.get_width(), y))
            y += linha

    def desenhar_ranking_completo(self):
        largura, altura = self.tela.get_size()
        k = self.k
        margem = int(48 * k)

        self.texto(self.fonte_logo, "verdia", pos=(margem, int(28 * k)))
        self.texto(self.fonte_titulo, "Ranking", centro=(largura // 2, int(130 * k)))

        ordem = self.ranking_ordenado()
        if not ordem:
            self.texto(self.fonte_media, "Ninguém jogou ainda", centro=(largura // 2, altura // 2), alfa=200)

        # Duas colunas, com quantas linhas couberem na tela
        topo = int(200 * k)
        linha = int(48 * k)
        por_coluna = max(1, (altura - topo - int(90 * k)) // linha)
        largura_coluna = (largura - 4 * margem) // 2

        for i, r in enumerate(ordem[:2 * por_coluna]):
            x = margem + (i // por_coluna) * (largura_coluna + 2 * margem)
            y = topo + (i % por_coluna) * linha
            cor = LIMA if r is self.ultima_entrada else BRANCO
            self.texto(self.fonte_media, f"{i + 1}", cor, pos=(x, y), alfa=170)
            self.texto(self.fonte_media, r["nome"], cor, pos=(x + int(64 * k), y))
            pontos = self.fonte_media.render(str(r["pontos"]), True, cor)
            self.tela.blit(pontos, (x + largura_coluna - pontos.get_width(), y))

        self.texto(self.fonte_pequena, "R ou ESC para voltar · Q sair",
                   pos=(margem, altura - int(44 * k)), alfa=150)

    def desenhar_progresso(self, cx, y):
        # Uma bolinha por LED da sequência; as já acertadas ficam preenchidas
        total = len(self.sequencia)
        passo = int(30 * self.k)
        raio = int(9 * self.k)
        x = cx - (total - 1) * passo // 2
        for i in range(total):
            centro = (x + i * passo, y)
            if i < self.posicao:
                pygame.draw.circle(self.tela, LIMA, centro, raio)
            else:
                pygame.draw.circle(self.tela, BRANCO, centro, raio, 2)

    def desenhar_jogo(self):
        largura, altura = self.tela.get_size()
        k = self.k
        margem = int(48 * k)
        coluna = int(largura * 0.36)
        cx = (largura - coluna - margem) // 2
        cy = altura // 2

        self.texto(self.fonte_logo, "verdia", pos=(margem, int(28 * k)))

        em_jogo = self.estado in ("INICIO", "MOSTRANDO", "JOGADOR", "ACERTOU")
        if em_jogo:
            self.texto(self.fonte_media, self.nome, centro=(cx, cy - int(160 * k)), alfa=200)
            self.texto(self.fonte_numero, str(self.pontos), centro=(cx, cy + int(150 * k)))
            self.texto(self.fonte_pequena, "pontos", centro=(cx, cy + int(200 * k)), alfa=200)

        if self.estado == "ESPERA":
            self.texto(self.fonte_titulo, "Pronto para jogar?", centro=(cx, cy - int(20 * k)))
            if self.calib:
                self.texto(self.fonte_media, "Pressione ENTER", centro=(cx, cy + int(60 * k)), alfa=210)
            else:
                self.texto(self.fonte_media, "Calibre a câmera com a tecla C", LIMA,
                           centro=(cx, cy + int(60 * k)))
            if self.aviso:
                self.texto(self.fonte_pequena, self.aviso, LIMA, centro=(cx, cy + int(120 * k)))

        elif self.estado == "NOME":
            self.texto(self.fonte_media, "Nome do jogador", centro=(cx, cy - int(90 * k)), alfa=210)
            cursor = "|" if int(self.agora * 2) % 2 == 0 else " "
            self.texto(self.fonte_titulo, self.nome + cursor, centro=(cx, cy))
            self.texto(self.fonte_pequena, "ENTER para começar", centro=(cx, cy + int(80 * k)), alfa=200)

        elif self.estado == "INICIO":
            self.texto(self.fonte_titulo, "Prepare-se", centro=(cx, cy - int(20 * k)))

        elif self.estado == "MOSTRANDO":
            self.texto(self.fonte_titulo, "Observe os LEDs", centro=(cx, cy - int(20 * k)))
            self.texto(self.fonte_media, f"Rodada {len(self.sequencia)}",
                       centro=(cx, cy + int(60 * k)), alfa=210)

        elif self.estado == "JOGADOR":
            if self.esperando_soltar and max(self.cobertura) >= LIMIAR_LIVRE:
                titulo = "Tire a mão da grama"
            else:
                titulo = "Sua vez"
            self.texto(self.fonte_titulo, titulo, centro=(cx, cy - int(20 * k)))
            self.desenhar_progresso(cx, cy + int(55 * k))

            resta = max(0.0, 1 - (self.agora - self.t_ultima) / TEMPO_LIMITE)
            barra = pygame.Rect(0, 0, int(360 * k), int(8 * k))
            barra.center = (cx, cy + int(95 * k))
            trilho = pygame.Surface(barra.size, pygame.SRCALPHA)
            pygame.draw.rect(trilho, (255, 255, 255, 70), trilho.get_rect(), border_radius=barra.height)
            self.tela.blit(trilho, barra.topleft)
            cheio = barra.copy()
            cheio.width = int(barra.width * resta)
            pygame.draw.rect(self.tela, LIMA, cheio, border_radius=barra.height)

        elif self.estado == "ACERTOU":
            self.texto(self.fonte_titulo, "Isso!", centro=(cx, cy - int(20 * k)))

        elif self.estado == "FIM":
            ordem = self.ranking_ordenado()
            lugar = next(i for i, r in enumerate(ordem) if r is self.ultima_entrada) + 1
            self.texto(self.fonte_titulo, "Fim de jogo", centro=(cx, cy - int(120 * k)))
            self.texto(self.fonte_media, self.motivo, centro=(cx, cy - int(50 * k)), alfa=210)
            self.texto(self.fonte_numero, f"{self.pontos} pontos", centro=(cx, cy + int(30 * k)))
            self.texto(self.fonte_media, f"{self.nome} ficou em {lugar}º lugar",
                       centro=(cx, cy + int(100 * k)))
            self.texto(self.fonte_pequena, "ENTER para o próximo jogador",
                       centro=(cx, cy + int(160 * k)), alfa=200)

        # Coluna da direita: ranking em cima, câmera embaixo
        x = largura - coluna - margem
        alt_camera = int(coluna * 3 / 4) + int(40 * k)
        rodape = int(36 * k)  # espaço para a linha de teclas
        area_camera = pygame.Rect(x, altura - margem - rodape - alt_camera, coluna, alt_camera)
        area_ranking = pygame.Rect(x, margem, coluna, area_camera.y - margem - int(20 * k))

        self.desenhar_ranking(area_ranking)
        self.cartao(area_camera)
        self.desenhar_camera(area_camera.inflate(-int(40 * k), -int(40 * k)))
        if self.calib:
            self.desenhar_zonas(self.calib["zonas"], True)

        self.texto(self.fonte_pequena, "ENTER jogador · R ranking · C calibrar · B nova referência · "
                   "F tela cheia · ESC voltar · Q sair", pos=(margem, altura - int(44 * k)), alfa=150)

    def desenhar_calibracao(self):
        largura, altura = self.tela.get_size()
        k = self.k
        margem = int(48 * k)

        if len(self.zonas_novas) < 3:
            proxima = len(self.zonas_novas)
            self.texto(self.fonte_media, f"Arraste um retângulo sobre a grama {NOMES_GRAMAS[proxima]}",
                       CORES_LED[proxima], pos=(margem, int(30 * k)))
        self.texto(self.fonte_pequena, "Ao soltar o terceiro, não pode haver mãos sobre a grama.  "
                   "ESC cancela.", pos=(margem, int(80 * k)), alfa=200)
        if self.aviso:
            self.texto(self.fonte_pequena, self.aviso, LIMA, pos=(margem, altura - int(44 * k)))

        area = pygame.Rect(margem, int(120 * k), largura - 2 * margem, altura - int(180 * k))
        self.desenhar_camera(area)
        self.desenhar_zonas(self.zonas_novas, False)

        if self.arraste:
            (x1, y1), (x2, y2) = self.arraste
            r = pygame.Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            pygame.draw.rect(self.tela, CORES_LED[len(self.zonas_novas)], r, 2, border_radius=6)

    def desenhar(self):
        self.tela.blit(self.fundo, (0, 0))
        if self.estado == "CALIBRANDO":
            self.desenhar_calibracao()
        elif self.estado == "RANKING":
            self.desenhar_ranking_completo()
        else:
            self.desenhar_jogo()
        pygame.display.flip()

    # ----- Laço principal -----

    def rodar(self):
        while self.rodando:
            self.agora = time.monotonic()

            # Enquanto um retângulo é arrastado, a imagem fica parada para o
            # laço não esperar a câmera e o arraste acompanhar o mouse
            if self.arraste is None:
                ok, frame = self.camera.read()
                if ok:
                    self.frame = frame
                    self.t_frame = self.agora
                elif self.agora - self.t_frame > 3:
                    # Câmera parou (cabo solto, outro app usando): reabre
                    self.frame = None
                    self.camera.release()
                    self.camera = abrir_camera()
                    self.t_frame = time.monotonic()

            self.medir()
            self.tratar_eventos()
            self.atualizar()
            self.desenhar()
            self.relogio.tick(60)

        self.enviar(b"0")
        self.camera.release()
        pygame.quit()


if __name__ == "__main__":
    Jogo().rodar()
