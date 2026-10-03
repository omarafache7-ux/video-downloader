from PIL import Image, ImageDraw

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# rounded gradient-ish background
d.rounded_rectangle((16, 16, S - 16, S - 16), radius=110, fill=(229, 57, 53))
d.rounded_rectangle((16, 16, S - 16, S // 2), radius=110, fill=(239, 83, 80))
d.rectangle((16, S // 2 - 110, S - 16, S // 2), fill=(239, 83, 80))
# down arrow
w = 255
d.rectangle((S // 2 - 45, 100, S // 2 + 45, 280), fill=w)
d.polygon([(S // 2 - 120, 260), (S // 2 + 120, 260), (S // 2, 390)], fill=(w, w, w))
# tray
d.rounded_rectangle((120, 400, S - 120, 430), radius=12, fill=(w, w, w))

img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save("icon.png")
