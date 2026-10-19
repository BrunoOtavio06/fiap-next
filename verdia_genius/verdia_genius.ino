// verdia — jogo da grama (bancada do NEXT)
//
// O Arduino só acende os LEDs. Quem sorteia a sequência, lê a câmera
// e conta os pontos é o jogo.py, que manda um caractere por comando:
//
//   v  acende só o verde        0  apaga tudo
//   a  acende só o amarelo      S  animação de início (~2 s)
//   r  acende só o vermelho     X  animação de fim de jogo (~2 s)
//                               I  modo espera (LEDs se revezando)
//
// Ligação: cada LED com o próprio resistor de 220 Ω.
// pino → resistor → perna longa do LED; perna curta → GND.

const int PINOS[3][2] = {
  {2, 3},  // verde    (grama pequena)
  {4, 5},  // amarelo  (grama média)
  {6, 7}   // vermelho (grama grande)
};

bool modoEspera = true;
int passoEspera = 0;
unsigned long ultimoPasso = 0;


void ligarCor(int cor, bool ligado) {
  digitalWrite(PINOS[cor][0], ligado ? HIGH : LOW);
  digitalWrite(PINOS[cor][1], ligado ? HIGH : LOW);
}


void ligarTodas(bool ligado) {
  for (int cor = 0; cor < 3; cor++) {
    ligarCor(cor, ligado);
  }
}


void acenderSo(int cor) {
  ligarTodas(false);
  ligarCor(cor, true);
}


void animacaoInicio() {
  for (int volta = 0; volta < 3; volta++) {
    for (int cor = 0; cor < 3; cor++) {
      acenderSo(cor);
      delay(150);
    }
  }

  ligarTodas(false);
  delay(200);

  ligarTodas(true);
  delay(500);

  ligarTodas(false);
}


void animacaoFim() {
  for (int vez = 0; vez < 4; vez++) {
    ligarTodas(true);
    delay(250);
    ligarTodas(false);
    delay(250);
  }
}


void setup() {
  for (int cor = 0; cor < 3; cor++) {
    pinMode(PINOS[cor][0], OUTPUT);
    pinMode(PINOS[cor][1], OUTPUT);
  }

  ligarTodas(false);
  Serial.begin(9600);
}


void loop() {
  if (Serial.available() > 0) {
    char comando = Serial.read();

    // quebras de linha (ex.: vindas do Serial Monitor) caem fora do switch
    switch (comando) {
      case 'v': modoEspera = false; acenderSo(0); break;
      case 'a': modoEspera = false; acenderSo(1); break;
      case 'r': modoEspera = false; acenderSo(2); break;
      case '0': modoEspera = false; ligarTodas(false); break;
      case 'S': modoEspera = false; animacaoInicio(); break;
      case 'X': modoEspera = false; animacaoFim(); break;
      case 'I': modoEspera = true; passoEspera = 0; ultimoPasso = 0; break;
    }
  }

  if (modoEspera && millis() - ultimoPasso >= 600) {
    ultimoPasso = millis();
    acenderSo(passoEspera);
    passoEspera = (passoEspera + 1) % 3;
  }
}
