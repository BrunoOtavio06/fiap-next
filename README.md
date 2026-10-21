# verdia — jogo da grama

Dinâmica da bancada do NEXT. O Arduino acende os LEDs, a webcam vê em qual grama a mão foi colocada e o `jogo.py` controla a partida, o placar e o ranking.

## Arquivos

| Arquivo | Para quê |
| --- | --- |
| `jogo.py` | Jogo, detecção pela câmera e placar |
| `Alice-Regular.ttf` | Fonte da marca (precisa ficar na mesma pasta do `jogo.py`) |
| `Alice-OFL.txt` | Licença da fonte |
| `verdia_genius/verdia_genius.ino` | Código do Arduino UNO, com a ligação dos LEDs no topo |

## Instalação

```bash
python3 -m venv venv
source venv/bin/activate
pip install opencv-python-headless==4.12.0.88 pygame pyserial
```

A versão do OpenCV é fixa: a partir da 4.13 ele traz uma cópia própria do SDL, que entra em conflito com a do pygame no Mac.

## Execução

```bash
source venv/bin/activate
python jogo.py
```

Na primeira vez, o macOS pede permissão para o Terminal usar a câmera. Libere e rode de novo.

## Variáveis de ambiente

| Variável | Padrão | Quando mudar |
| --- | --- | --- |
| `VERDIA_CAMERA` | `0` | Se abrir a câmera errada (a do Mac ou a do iPhone). Teste `1`, `2`... até aparecer a webcam do tripé. |
| `VERDIA_PORTA` | detecta sozinho | Se o Arduino não for encontrado. Liste as portas com `ls /dev/cu.*` e use a que tem `usbmodem`. |

Exemplo:

```bash
VERDIA_CAMERA=1 VERDIA_PORTA=/dev/cu.usbmodem14101 python jogo.py
```

Sem Arduino conectado, o jogo roda normalmente, só sem os LEDs.

## Teclas

| Tecla | Ação |
| --- | --- |
| `ENTER` | Novo jogador (digite o nome e `ENTER` de novo para começar) |
| `R` | Mostrar o ranking completo (na tela inicial ou no fim de jogo) |
| `C` | Calibrar a câmera |
| `B` | Recalibrar só o verde, se a luz do local mudar |
| `F` | Tela cheia |
| `ESC` | Cancelar e voltar à tela inicial |
| `Q` | Sair do jogo e apagar os LEDs |

## Calibração

Com a câmera já no tripé, aperte `C` e arraste um retângulo sobre cada grama, na ordem pequena, média e grande. Ao soltar o terceiro, não pode haver mão sobre a grama.

Na imagem da câmera, cada região mostra quanto está coberta: perto de 0% sem mão e acima de 35% com a mão em cima. Se não ficar assim, ajuste `LIMIAR_MAO` e a faixa `VERDE_MIN`/`VERDE_MAX` no topo do `jogo.py`.

## Dados salvos

- `calibracao.json`: regiões da câmera. Só muda ao calibrar de novo.
- `ranking.json`: todas as partidas. Apague antes do evento para zerar.
