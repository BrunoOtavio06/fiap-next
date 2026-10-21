import os
import time

import cv2

print("VERDIA_CAMERA =", os.environ.get("VERDIA_CAMERA"))

for indice in range(4):
    camera = cv2.VideoCapture(indice)
    time.sleep(1)

    ok, frame = False, None
    for _ in range(10):
        ok, frame = camera.read()
        if ok:
            break
        time.sleep(0.2)

    if not camera.isOpened():
        print(indice, "não abriu")
    elif not ok or frame is None:
        print(indice, "abriu, mas não mandou imagem")
    else:
        altura, largura = frame.shape[:2]
        print(indice, f"ok, {largura}x{altura}, brilho médio {frame.mean():.0f}")
        cv2.imwrite(f"camera_{indice}.jpg", frame)

    camera.release()
