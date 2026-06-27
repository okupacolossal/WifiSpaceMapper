import serial
ser = serial.Serial("COM9", 921600)

while True:
    line = ser.readline().decode(errors="ignore")

    if not line.startswith("CSI_DATA"):
        continue

    parts = line.split("[")[1]
    parts = parts.split("]")[0] 
    nums = [int(x) for x in parts.split()]