"""Contact sheet: python tools/sheet.py out.jpg N file1 file2 ...  (N = every Nth file)"""
import sys

import cv2
import numpy as np

out, step, files = sys.argv[1], int(sys.argv[2]), sorted(sys.argv[3:])[:: int(sys.argv[2])][:24]
tiles = []
for f in files:
    im = cv2.resize(cv2.imread(f), (320, 180))
    cv2.putText(im, f.rsplit("/", 1)[-1][:12], (5, 15), 0, 0.5, (0, 0, 255), 1)
    tiles.append(im)
while len(tiles) % 4:
    tiles.append(np.zeros_like(tiles[0]))
cv2.imwrite(out, np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))
