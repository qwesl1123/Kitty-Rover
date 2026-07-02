// Kitty Rover — ESP32 drivetrain firmware (tank drive)
// Protocol over USB serial @115200:  "M <left> <right>\n"  values -255..255
//                                     "stop" or "s"  -> halt

// ---- pin map ----
const int L_RPWM = 25, L_LPWM = 26;   // LEFT  IBT-2
const int R_RPWM = 32, R_LPWM = 33;   // RIGHT IBT-2
const int EN     = 27;                // shared: R_EN + L_EN on BOTH boards

// ---- direction convention (positive = forward for both tracks) ----
const bool L_INVERT = false;
const bool R_INVERT = true;           // right motor is mounted mirrored

// ---- safety ----
const unsigned long DEADMAN_MS = 500; // no command for this long -> stop
unsigned long lastCmd = 0;

void setMotor(int rpwm, int lpwm, int speed, bool invert) {
  if (invert) speed = -speed;
  speed = constrain(speed, -255, 255);
  if (speed >= 0) { analogWrite(lpwm, 0); analogWrite(rpwm, speed); }
  else            { analogWrite(rpwm, 0); analogWrite(lpwm, -speed); }
}

void stopAll() {
  analogWrite(L_RPWM, 0); analogWrite(L_LPWM, 0);
  analogWrite(R_RPWM, 0); analogWrite(R_LPWM, 0);
}

void drive(int left, int right) {
  setMotor(L_RPWM, L_LPWM, left,  L_INVERT);
  setMotor(R_RPWM, R_LPWM, right, R_INVERT);
}

void setup() {
  pinMode(EN, OUTPUT);
  digitalWrite(EN, HIGH);             // enable both H-bridges
  stopAll();
  Serial.begin(115200);
  Serial.println("Rover ready. Send: M <left> <right>   e.g.  M 150 150");
  lastCmd = millis();
}

String line;
void loop() {
  if (Serial.available()) {
    line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      if (line.charAt(0) == 'M' || line.charAt(0) == 'm') {
        int sp1 = line.indexOf(' ');
        int sp2 = line.indexOf(' ', sp1 + 1);
        if (sp1 > 0 && sp2 > sp1) {
          int left  = line.substring(sp1 + 1, sp2).toInt();
          int right = line.substring(sp2 + 1).toInt();
          drive(left, right);
          lastCmd = millis();
          Serial.print("L="); Serial.print(left);
          Serial.print(" R="); Serial.println(right);
        } else Serial.println("format: M <left> <right>");
      } else if (line == "stop" || line == "s") {
        drive(0, 0); lastCmd = millis(); Serial.println("stopped");
      }
    }
  }
  if (millis() - lastCmd > DEADMAN_MS) stopAll();   // deadman
}
