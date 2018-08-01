import RPi.GPIO as GPIO
from lib_nrf24 import NRF24
import spidev
from wsserver import WebsocketServer

import WalabotAPI as bot
from math import *
import time
from os import system
import threading


# Setup the nRF24L01 module

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

radio = NRF24(GPIO, spidev.SpiDev())
radio.begin(0, 17)
time.sleep(1)
radio.setPayloadSize(3)
radio.setChannel(0x60)
radio.setDataRate(NRF24.BR_1MBPS)
radio.setPALevel(NRF24.PA_MAX)
radio.setAutoAck(True)
# radio.setRetries(15, 15) # this doesn't need to be commented, but it makes the cars respond faster when commented
radio.stopListening()


# throttleFun is used to convert a linear throttle value -1 to 1 to an exponential curve in the same bounds.
# it allows the cars to respond to different speed changes better

def throttleFunc(x):
	k = 4
	return (x ** 3 * (k - 1) + x) / k


# used to define and control a physical Car

class Car:

	def __init__(self, address):
		self.address = address
		self.steering = 0
		self.throttle = 0
		self.reverse = False

	# creates the byte packet that will be sent on the nRF24 module
	# contains steering, throttle, and forward/reverse

	def sendPacket(self):
		packet = [
			int(self.steering),
			abs(int(throttleFunc(self.throttle) * 255)),
			int(1 if self.reverse else 0)
		]
		radio.openWritingPipe(self.address)
		radio.write(packet)
		radio.flush_tx()


# the list of cars this base station can talk to
# you can easily add more, just give each car a new address
# if you have more cars, you'll need to add them to the HTML code in index.html

cars = [Car([0xe0, 0xe0, 0xe0, 0xe0, 0xf2]), Car([0xe0, 0xe0, 0xe0, 0xe0, 0xf3])]
connectedIp = None
nowCarId = 0
nowCarGear = 0


# called when a WebSocket connects to this server
# just makes sure only the packets from one mobile device are being used

def wsNewClient(client, server):
	global connectedIp
	if connectedIp == None:
		connectedIp = client["address"][0]
		return

# called when a WebSocket disconnects from the server
# frees the controller for another mobile device to use

def wsClientLeft(client, server):
	global connectedIp
	if client["address"][0] == connectedIp:
		connectedIp = None

# called when a packet is received from the mobile device
# [1, Car#]: set car to Car#, and set the gear for that car
# [2, Gear#]: set gear to Gear#
# [3, Steering]: set the steering of the current car to Steering

def wsMsgRecv(client, server, msg):
	global connectedIp, nowCarId, nowCarGear
	if client["address"][0] == connectedIp:
		packet = list(msg)
		if len(packet) != 2:
			return
		if packet[0] == 1:
			#set car
			nowCarId = packet[1]
			car = cars[nowCarId]
			if nowCarGear == 0:
				car.steering = 0
				car.throttle = 0
			elif nowCarGear == 1:
				car.reverse = True
			elif nowCarGear == 2:
				car.throttle = 0
			else:
				car.reverse = False
			car.sendPacket()
		elif packet[0] == 2:
			# set gear
			car = cars[nowCarId]
			nowCarGear = packet[1]
			if nowCarGear == 0:
				car.steering = 0
				car.throttle = 0
			elif nowCarGear == 1:
				car.reverse = True
			elif nowCarGear == 2:
				car.throttle = 0
			else:
				car.reverse = False
			car.sendPacket()
		elif packet[0] == 3:
			car = cars[nowCarId]
			car.steering = packet[1]
			car.sendPacket()


# setup the WebSocketServer to this Raspberry Pi
# the host is '[hostname].local'
# hostname can be found by typing 'hostname' into the terminal on this Pi

server = WebsocketServer(8080, host='raspberrypi2.local')
server.set_fn_new_client(wsNewClient)
server.set_fn_client_left(wsClientLeft)
server.set_fn_message_received(wsMsgRecv)

# start the server on a separate thread so we can scan with the Walabot on the main thread

serverThread = threading.Thread(target=server.serve_forever)
serverThread.start()





# begin the Walabot initialization

print("[initialize]")
bot.Init()
bot.Initialize()

# connect to any Walabot over USB

print("[connect]")
try:
	bot.ConnectAny()
except bot.WalabotError as e:
	print(e)
	exit()


# Set the configuration to be a very small cone just above the Walabot device

print("[configure]")
bot.SetProfile(bot.PROF_TRACKER)
bot.SetArenaR(5, 20, 0.2)
bot.SetArenaTheta(-1, 1, 1)	
bot.SetArenaPhi(-10, 10, 10)
bot.SetThreshold(30)
bot.SetDynamicImageFilter(bot.FILTER_TYPE_NONE)
bot.Start()


# calibrate the Walabot to the background noise
# after calibration, it will only sense the distance your foot is from it.

print("[calibrate]")
bot.StartCalibration()
while bot.GetStatus()[0] == bot.STATUS_CALIBRATING:
	bot.Trigger()
print()


# start scanning the distance from your foot using the image data from the Walabot

print("[scan]")
while True:

	bot.Trigger()
	img, _, _, _, _ = bot.GetRawImageSlice() # use the raw image data of the scan

	# each row corrosponds to distance from the sensor
	# we just need to find which row is the first one that contains your foot

	minRow = 0
	for row in img:
		if sum(row) / len(row) < 25:
			minRow += 1

	# the pedal value is calculated by scaling the percentage value of the minRow of the number of rows in the whole image

	pedal = ((minRow - 3) / len(img)) * 2.1

	# invert the pedal value so 1 becomes 0 and 0 becomes 1
	# fix it to between 0 and 1

	pedal = min(max(1 - pedal, 0), 1)

	# if we're in Reverse, or Drive, send the throttle value to the mobile device's WebSocket, and set the throttle for the current car

	if nowCarGear == 1 or nowCarGear == 3:
		server.send_message_to_all(bytes([int(pedal * 255)]))
		car = cars[nowCarId]
		car.throttle = pedal
		car.sendPacket()



# this will only happen after the program is stopped

# Stop and Disconnect the Walabot with the API

bot.Stop()
bot.Disconnect()
