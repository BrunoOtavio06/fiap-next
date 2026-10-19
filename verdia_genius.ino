// verdia - jogo da grama (bancada do NEXT)
//
// O Arduino so obedece comandos que o computador manda pela USB.
// Quem sorteia a sequencia, le a camera e conta os pontos e o Python.
//
// Comandos (1 caractere cada):
//   v  acende so o verde
//   a  acende so o amarelo
//   r  acende so o vermelho
//   0  apaga tudo
//   S  animacao de start (dura ~2 s)
//   X  animacao de game over (dura ~2 s)
//   I  modo espera (LEDs passeando devagar ate chegar outro comando)
//
// Ligacao: cada LED com seu proprio resistor de 220 ohm.
//   pino -> resistor -> perna longa do LED; perna curta -> GND

const int PINOS[3][2] = {
  {2, 3},  // verde    (grama pequena)
  {4, 5},  // amarelo  (grama media)
  {6, 7}   // vermelho (grama grande)
};

bool modoEspera = true;
int passoEspera = 0;
unsigned long ultimoPasso = 0;


void ligarCor(int cor, bool ligado) {
  digitalWrite(PINOS[cor][0], ligado ? HIGH : LOW);
  digitalWrite(PINOS[cor][1], ligado ? HIGH : LOW);
}


void apagarTudo() {
  for (int cor = 0; cor < 3; cor++) {
    ligarCor(cor, false);
  }
}


void acenderTudo() {
  for (int cor = 0; cor < 3; cor++) {
    ligarCor(cor, true);
  }
}


void acenderSo(int cor) {
  apagarTudo();
  ligarCor(cor, true);
}


void animacaoStart() {
  // 3 voltas rapidas verde -> amarelo -> vermelho
  for (int volta = 0; volta < 3; volta++) {
    for (int cor = 0; cor < 3; cor++) {
      acenderSo(cor);
      delay(150);
    }
  }

  apagarTudo();
  delay(200);

  acenderTudo();
  delay(500);

  apagarTudo();
}


void animacaoGameOver() {
  // os tres piscam juntos 4 vezes
  for (int vez = 0; vez < 4; vez++) {
    acenderTudo();
    delay(250);
    apagarTudo();
    delay(250);
  }
}


void setup() {
  for (int cor = 0; cor < 3; cor++) {
    pinMode(PINOS[cor][0], OUTPUT);
    pinMode(PINOS[cor][1], OUTPUT);
  }

  apagarTudo();
  Serial.begin(9600);
}


void loop() {
  if (Serial.available() > 0) {
    char comando = Serial.read();

    switch (comando) {
      case 'v':
        modoEspera = false;
        acenderSo(0);
        break;

      case 'a':
        modoEspera = false;
        acenderSo(1);
        break;

      case 'r':
        modoEspera = false;
        acenderSo(2);
        break;

      case '0':
        modoEspera = false;
        apagarTudo();
        break;

      case 'S':
        modoEspera = false;
        animacaoStart();
        break;

      case 'X':
        modoEspera = false;
        animacaoGameOver();
        break;

      case 'I':
        modoEspera = true;
        passoEspera = 0;
        ultimoPasso = 0;
        break;
    }
  }

  // modo espera: um LED de cada vez, devagar
  if (modoEspera && millis() - ultimoPasso >= 600) {
    ultimoPasso = millis();
    acenderSo(passoEspera);
    passoEspera = (passoEspera + 1) % 3;
  }
}
