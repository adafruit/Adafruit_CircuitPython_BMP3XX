import time
import board
import busio
import adafruit_bmp3xx

i2c = busio.I2C(board.SCL, board.SDA)

bmp = adafruit_bmp3xx.BMP3XX_I2C(i2c)

bmp.pressure_oversampling = 8
bmp.temperature_oversampling = 2

while True:
    print("Pressure: {:6.1f}  Temperature: {:5.2f}".format(bmp.pressure, bmp.temperature))
    time.sleep(1)
