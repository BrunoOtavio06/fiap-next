# verdia - jogo da grama (bancada do NEXT)
#
# O computador comanda o jogo: sorteia a sequencia, manda o Arduino acender
# os LEDs, olha a camera para ver em qual grama a pessoa colocou a mao e
# mostra o placar e o ranking.
#
# Teclas (equipe):
#   ENTER  novo jogador (digita o nome e ENTER de novo para comecar)
#   C      calibrar a camera (desenhar as 3 regioes)
#   B      recalibrar so o verde (se a luz do local mudar)
#   F      tela cheia / janela
#   ESC    cancelar e voltar para a tela inicial
#
# Instalar: pip install opencv-python pygame pyserial

import json
import os
import random
import time

import cv2
import numpy as np
import pygame
import serial
import serial.tools.list_ports


# ------------------------------------------------------------------
# Configuracoes
# ------------------------------------------------------------------

CAMERA = 0                # troque para 1, 2... se abrir a camera errada
MONITOR = 0               # 1 se a tela cheia tiver que ir para a TV/segundo monitor
PORTA_ARDUINO = None      # None = procura sozinho. Ou ex.: "/dev/cu.usbmodem14101"

TEMPO_LED_ACESO = 0.6     # segundos que cada LED fica aceso na sequencia
TEMPO_LED_APAGADO = 0.3   # intervalo entre um LED e outro
TEMPO_ANTES_SEQUENCIA = 1.0
TEMPO_CONFIRMA = 0.4      # mao parada numa grama por esse tempo = jogada
TEMPO_MAO_FORA = 0.2      # todas as gramas livres por esse tempo = mao saiu
TEMPO_LIMITE = 5.0        # sem jogada por esse tempo = game over
TEMPO_START = 2.5         # a animacao de start do Arduino dura ~2 s
TEMPO_GAME_OVER = 8.0     # tempo da tela de fim de jogo

LIMIAR_MAO = 0.35         # parte da regiao coberta para contar como mao
LIMIAR_LIVRE = 0.15       # abaixo disso a regiao esta livre

# faixa de verde em HSV (no OpenCV o H vai de 0 a 179)
VERDE_MIN = np.array([30, 40, 30])
VERDE_MAX = np.array([90, 255, 255])

NOMES_ZONAS = ["pequena (verde)", "média (amarelo)", "grande (vermelho)"]
CORES = ["verde", "amarelo", "vermelho"]
COMANDO_COR = [b"v", b"a", b"r"]
COR_ZONA = [(70, 190, 90), (240, 200, 50), (225, 60, 60)]

FUNDO = (18, 38, 28)
TEXTO = (235, 240, 230)
TEXTO_FRACO = (150, 170, 155)
DESTAQUE = (130, 200, 120)

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQ_CALIBRACAO = os.path.join(PASTA, "calibracao.json")
ARQ_RANKING = os.path.join(PASTA, "ranking.json")


# ------------------------------------------------------------------
# Arquivos, Arduino e camera
# ------------------------------------------------------------------

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
    time.sleep(2)  # o UNO reinicia quando a porta e aberta
    print("Arduino conectado em", porta)
    return conexao


def fracao_verde(hsv, zona):
    x, y, w, h = zona
    recorte = hsv[y:y + h, x:x + w]
    mascara = cv2.inRange(recorte, VERDE_MIN, VERDE_MAX)
    return cv2.countNonZero(mascara) / mascara.size


# ------------------------------------------------------------------
# Jogo
# ------------------------------------------------------------------

class Jogo:

    def __init__(self):
        pygame.init()
        pygame.key.stop_text_input()
        self.tela = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        pygame.display.set_caption("verdia")
        self.tela_cheia = False
        self.relogio = pygame.time.Clock()

        fonte = "helveticaneue,helvetica,arial"
        self.fonte_titulo = pygame.font.SysFont("alice,georgia", 64)
        self.fonte_grande = pygame.font.SysFont(fonte, 72, bold=True)
        self.fonte_media = pygame.font.SysFont(fonte, 38)
        self.fonte_pequena = pygame.font.SysFont(fonte, 22)

        self.camera = cv2.VideoCapture(CAMERA)
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.frame = None
        self.vista = None
        self.cobertura = [0.0, 0.0, 0.0]

        self.arduino = conectar_arduino()
        self.led_aceso = None

        self.calib = carregar_json(ARQ_CALIBRACAO, None)
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
        self.t_evento = 0
        self.t_ultima = 0
        self.t_livre = None
        self.zona_atual = None
        self.t_zona = 0
        self.esperando_soltar = False

        self.zonas_novas = []
        self.arraste = None

        self.enviar(b"I")

    # ---------------- LEDs ----------------

    def enviar(self, comando):
        if self.arduino is None:
            return
        try:
            self.arduino.write(comando)
        except serial.SerialException:
            print("Perdi a conexão com o Arduino. Seguindo sem os LEDs.")
            self.arduino = None

    def acender(self, cor):
        self.led_aceso = cor
        self.enviar(COMANDO_COR[cor])

    def apagar(self):
        self.led_aceso = None
        self.enviar(b"0")

    # ---------------- Camera ----------------

    def medir(self):
        if self.frame is None or self.calib is None:
            self.cobertura = [0.0, 0.0, 0.0]
            return

        h, w = self.frame.shape[:2]
        if [w, h] != self.calib["tamanho"]:
            self.calib = None
            self.aviso = "A resolução da câmera mudou. Calibre de novo (C)."
            return

        hsv = cv2.cvtColor(self.frame, cv2.COLOR_BGR2HSV)
        for i in range(3):
            verde = fracao_verde(hsv, self.calib["zonas"][i])
            base = self.calib["base"][i]
            self.cobertura[i] = min(1.0, max(0.0, 1 - verde / base))

    def medir_base(self, zonas):
        # quanto de verde cada regiao tem sem nenhuma mao em cima
        hsv = cv2.cvtColor(self.frame, cv2.COLOR_BGR2HSV)
        base = [fracao_verde(hsv, z) for z in zonas]

        for i in range(3):
            if base[i] < 0.3:
                self.aviso = (f"A região {CORES[i]} quase não tem verde ({base[i]:.0%}). "
                              "Desenhe só sobre a grama, sem mãos em cima.")
                return None

        return base

    # ---------------- Fluxo do jogo ----------------

    def voltar_espera(self):
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
        self.estado = "START"
        self.t_evento = self.agora + TEMPO_START

    def nova_rodada(self):
        self.sequencia.append(random.randrange(3))
        self.posicao = 0
        self.apagar()
        self.estado = "MOSTRANDO"
        self.t_evento = self.agora + TEMPO_ANTES_SEQUENCIA

    def vez_do_jogador(self):
        self.estado = "JOGADOR"
        self.posicao = 0
        self.esperando_soltar = True  # quem ja estava com a mao na grama precisa tirar
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
            self.fim_de_jogo("Tempo esgotado")
            return

        if self.esperando_soltar:
            if self.mao_fora():
                self.esperando_soltar = False
                self.apagar()
            return

        cobertas = [i for i in range(3) if self.cobertura[i] >= LIMIAR_MAO]

        # so conta quando exatamente uma grama esta coberta
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
            self.fim_de_jogo(f"Era a grama {NOMES_ZONAS[certa]}")
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
        self.estado = "GAMEOVER"
        self.t_evento = self.agora + TEMPO_GAME_OVER

        entrada = {
            "nome": self.nome.strip(),
            "pontos": self.pontos,
            "hora": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.ranking.append(entrada)
        salvar_json(ARQ_RANKING, self.ranking)
        self.ultima_entrada = entrada

    def atualizar(self):
        if self.estado == "START":
            if self.agora >= self.t_evento:
                self.nova_rodada()

        elif self.estado == "MOSTRANDO":
            if self.agora >= self.t_evento:
                if self.led_aceso is None:
                    if self.posicao < len(self.sequencia):
                        self.acender(self.sequencia[self.posicao])
                        self.t_evento = self.agora + TEMPO_LED_ACESO
                    else:
                        self.vez_do_jogador()
                else:
                    self.apagar()
                    self.posicao += 1
                    self.t_evento = self.agora + TEMPO_LED_APAGADO

        elif self.estado == "JOGADOR":
            self.ler_jogada()

        elif self.estado == "ACERTOU":
            if self.mao_fora():
                if self.led_aceso is not None:
                    self.apagar()
                if self.agora >= self.t_evento:
                    self.nova_rodada()

        elif self.estado == "GAMEOVER":
            if self.agora >= self.t_evento:
                self.voltar_espera()

    # ---------------- Teclado e mouse ----------------

    def tratar_eventos(self):
        for evento in pygame.event.get():
            if evento.type == pygame.QUIT:
                self.rodando = False

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

        elif k == pygame.K_f and self.estado != "NOME":
            self.alternar_tela_cheia()

        elif self.estado == "ESPERA":
            if enter and self.calib:
                self.pedir_nome()
            elif k == pygame.K_c:
                self.iniciar_calibracao()
            elif k == pygame.K_b and self.calib and self.frame is not None:
                base = self.medir_base(self.calib["zonas"])
                if base:
                    self.calib["base"] = base
                    salvar_json(ARQ_CALIBRACAO, self.calib)
                    self.aviso = "Verde recalibrado."

        elif self.estado == "NOME":
            if k == pygame.K_BACKSPACE:
                self.nome = self.nome[:-1]
            elif enter and self.nome.strip():
                self.iniciar_partida()

        elif self.estado == "GAMEOVER":
            if enter:
                self.pedir_nome()

    def alternar_tela_cheia(self):
        self.tela_cheia = not self.tela_cheia
        if self.tela_cheia:
            self.tela = pygame.display.set_mode((0, 0), pygame.FULLSCREEN, display=MONITOR)
        else:
            self.tela = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)

    def iniciar_calibracao(self):
        self.aviso = ""
        self.zonas_novas = []
        self.arraste = None
        self.estado = "CALIBRANDO"

    def tela_para_camera(self, ponto):
        x0, y0, escala = self.vista
        h, w = self.frame.shape[:2]
        x = int((ponto[0] - x0) / escala)
        y = int((ponto[1] - y0) / escala)
        return min(max(x, 0), w), min(max(y, 0), h)

    def mouse_calibracao(self, evento):
        if self.frame is None or self.vista is None:
            return

        if evento.type == pygame.MOUSEBUTTONDOWN and evento.button == 1:
            self.arraste = [evento.pos, evento.pos]

        elif evento.type == pygame.MOUSEMOTION and self.arraste:
            self.arraste[1] = evento.pos

        elif evento.type == pygame.MOUSEBUTTONUP and evento.button == 1 and self.arraste:
            x1, y1 = self.tela_para_camera(self.arraste[0])
            x2, y2 = self.tela_para_camera(evento.pos)
            self.arraste = None

            zona = [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]
            if zona[2] < 10 or zona[3] < 10:
                return

            self.aviso = ""
            self.zonas_novas.append(zona)

            if len(self.zonas_novas) == 3:
                base = self.medir_base(self.zonas_novas)
                if base is None:
                    self.zonas_novas = []
                    return

                h, w = self.frame.shape[:2]
                self.calib = {"tamanho": [w, h], "zonas": self.zonas_novas, "base": base}
                salvar_json(ARQ_CALIBRACAO, self.calib)
                self.aviso = "Câmera calibrada."
                self.voltar_espera()

    # ---------------- Desenho ----------------

    def texto(self, fonte, conteudo, cor, centro=None, pos=None):
        img = fonte.render(conteudo, True, cor)
        rect = img.get_rect(center=centro) if centro else img.get_rect(topleft=pos)
        self.tela.blit(img, rect)

    def desenhar_camera(self, area):
        if self.frame is None:
            self.vista = None
            self.texto(self.fonte_pequena, "Sem imagem da câmera", TEXTO_FRACO, centro=area.center)
            return

        h, w = self.frame.shape[:2]
        escala = min(area.width / w, area.height / h)
        tamanho = (int(w * escala), int(h * escala))
        x0 = area.x + (area.width - tamanho[0]) // 2
        y0 = area.y + (area.height - tamanho[1]) // 2

        rgb = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
        img = pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")
        self.tela.blit(pygame.transform.smoothscale(img, tamanho), (x0, y0))
        self.vista = (x0, y0, escala)

    def desenhar_zonas(self, zonas, com_cobertura):
        if self.vista is None:
            return

        x0, y0, escala = self.vista
        for i, (x, y, w, h) in enumerate(zonas):
            r = pygame.Rect(x0 + x * escala, y0 + y * escala, w * escala, h * escala)
            pygame.draw.rect(self.tela, COR_ZONA[i], r, 6 if self.led_aceso == i else 2)
            if com_cobertura:
                self.texto(self.fonte_pequena, f"{self.cobertura[i]:.0%}", COR_ZONA[i],
                           pos=(r.x + 6, r.y + 4))

    def ranking_ordenado(self):
        return sorted(self.ranking, key=lambda r: (-r["pontos"], r["hora"]))

    def desenhar_ranking(self, x, y, largura):
        self.texto(self.fonte_media, "Ranking", TEXTO, pos=(x, y))
        y += 60

        for i, r in enumerate(self.ranking_ordenado()[:10]):
            cor = DESTAQUE if r is self.ultima_entrada else TEXTO
            self.texto(self.fonte_pequena, f"{i + 1}. {r['nome']}", cor, pos=(x, y))
            pontos = self.fonte_pequena.render(str(r["pontos"]), True, cor)
            self.tela.blit(pontos, (x + largura - pontos.get_width(), y))
            y += 32

    def desenhar_jogo(self):
        largura, altura = self.tela.get_size()
        divisa = int(largura * 0.62)
        cx = divisa // 2
        cy = altura // 2

        self.texto(self.fonte_titulo, "verdia", DESTAQUE, pos=(50, 30))

        if self.estado in ("START", "MOSTRANDO", "JOGADOR", "ACERTOU"):
            self.texto(self.fonte_media, self.nome, TEXTO_FRACO, centro=(cx, cy - 170))
            self.texto(self.fonte_media, f"Pontos: {self.pontos}", TEXTO, centro=(cx, cy + 120))

        if self.estado == "ESPERA":
            self.texto(self.fonte_grande, "Pronto para jogar?", TEXTO, centro=(cx, cy - 30))
            if self.calib:
                self.texto(self.fonte_media, "ENTER: novo jogador", TEXTO_FRACO, centro=(cx, cy + 50))
            else:
                self.texto(self.fonte_media, "Câmera não calibrada: aperte C", COR_ZONA[2],
                           centro=(cx, cy + 50))
            if self.aviso:
                self.texto(self.fonte_pequena, self.aviso, COR_ZONA[1], centro=(cx, cy + 110))

        elif self.estado == "NOME":
            self.texto(self.fonte_media, "Nome do jogador", TEXTO_FRACO, centro=(cx, cy - 80))
            cursor = "|" if int(self.agora * 2) % 2 == 0 else " "
            self.texto(self.fonte_grande, self.nome + cursor, TEXTO, centro=(cx, cy))
            self.texto(self.fonte_pequena, "ENTER para começar", TEXTO_FRACO, centro=(cx, cy + 80))

        elif self.estado == "START":
            self.texto(self.fonte_grande, "Prepare-se!", TEXTO, centro=(cx, cy - 30))

        elif self.estado == "MOSTRANDO":
            self.texto(self.fonte_grande, "Observe os LEDs", TEXTO, centro=(cx, cy - 30))
            self.texto(self.fonte_media, f"Rodada {len(self.sequencia)}", TEXTO_FRACO,
                       centro=(cx, cy + 50))

        elif self.estado == "JOGADOR":
            if self.esperando_soltar and max(self.cobertura) >= LIMIAR_LIVRE:
                titulo = "Tire a mão da grama"
            else:
                titulo = "Sua vez!"
            self.texto(self.fonte_grande, titulo, TEXTO, centro=(cx, cy - 30))
            self.texto(self.fonte_media, f"{self.posicao} de {len(self.sequencia)}", TEXTO_FRACO,
                       centro=(cx, cy + 50))

            # barra do tempo restante
            resta = max(0.0, 1 - (self.agora - self.t_ultima) / TEMPO_LIMITE)
            barra = pygame.Rect(cx - 200, cy + 170, 400, 14)
            pygame.draw.rect(self.tela, (50, 70, 58), barra)
            pygame.draw.rect(self.tela, DESTAQUE, (barra.x, barra.y, int(400 * resta), 14))

        elif self.estado == "ACERTOU":
            self.texto(self.fonte_grande, "Isso!", DESTAQUE, centro=(cx, cy - 30))

        elif self.estado == "GAMEOVER":
            self.texto(self.fonte_grande, "Fim de jogo", COR_ZONA[2], centro=(cx, cy - 110))
            self.texto(self.fonte_media, self.motivo, TEXTO_FRACO, centro=(cx, cy - 40))
            self.texto(self.fonte_media, f"{self.nome}: {self.pontos} pontos", TEXTO,
                       centro=(cx, cy + 30))
            ordem = self.ranking_ordenado()
            lugar = next(i for i, r in enumerate(ordem) if r is self.ultima_entrada) + 1
            self.texto(self.fonte_media, f"{lugar}º lugar no ranking", DESTAQUE, centro=(cx, cy + 85))
            self.texto(self.fonte_pequena, "ENTER: próximo jogador", TEXTO_FRACO,
                       centro=(cx, cy + 150))

        # coluna da direita: ranking em cima, camera embaixo
        x = divisa + 20
        largura_coluna = largura - divisa - 50
        self.desenhar_ranking(x, 40, largura_coluna)

        area_camera = pygame.Rect(x, altura - 40 - largura_coluna * 3 // 4,
                                  largura_coluna, largura_coluna * 3 // 4)
        self.desenhar_camera(area_camera)
        if self.calib:
            self.desenhar_zonas(self.calib["zonas"], True)

        self.texto(self.fonte_pequena, "ENTER jogador   C calibrar   B recalibrar verde   "
                   "F tela cheia   ESC voltar", (80, 100, 88), pos=(50, altura - 40))

    def desenhar_calibracao(self):
        largura, altura = self.tela.get_size()

        if len(self.zonas_novas) < 3:
            proxima = len(self.zonas_novas)
            self.texto(self.fonte_media, f"Arraste um retângulo sobre a grama {NOMES_ZONAS[proxima]}",
                       COR_ZONA[proxima], pos=(40, 25))
        self.texto(self.fonte_pequena, "Sem mãos em cima da grama ao terminar.   ESC cancela",
                   TEXTO_FRACO, pos=(40, 75))
        if self.aviso:
            self.texto(self.fonte_pequena, self.aviso, COR_ZONA[2], pos=(40, altura - 35))

        self.desenhar_camera(pygame.Rect(40, 110, largura - 80, altura - 160))
        self.desenhar_zonas(self.zonas_novas, False)

        if self.arraste:
            (x1, y1), (x2, y2) = self.arraste
            r = pygame.Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            pygame.draw.rect(self.tela, COR_ZONA[len(self.zonas_novas)], r, 2)

    def desenhar(self):
        self.tela.fill(FUNDO)
        if self.estado == "CALIBRANDO":
            self.desenhar_calibracao()
        else:
            self.desenhar_jogo()
        pygame.display.flip()

    # ---------------- Laco principal ----------------

    def rodar(self):
        while self.rodando:
            self.agora = time.monotonic()

            ok, frame = self.camera.read()
            if ok:
                self.frame = frame

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
