import pyHook
import pythoncom
import os, time

class KeyLogger(object):
	def __init__(self):
		self.window = None
		self.events = []
		self.pressed = []
		self.start_time = 0
		self.keylog = None
		self.manager = pyHook.HookManager()
		self.manager.KeyDown = self.__pressed
		self.manager.KeyUp = self.__released
	def __pressed(self, event):
		if self.keylog and event.WindowName == self.window:
			self.events.append((time.monotonic(),event))
		return True
	def __released(self, event):
		if self.keylog and event.WindowName == self.window:
			self.events.append((time.monotonic(),event))
		return True
	def start(self, window, start_time, directory):
		if not self.keylog:
			self.events = []
			self.pressed = []
			self.window = window
			self.start_time = start_time
			self.keylog = open(os.path.join(directory, "keylog.txt"), 'w')
			self.manager.HookKeyboard()
	def stop(self):
		self.manager.UnhookKeyboard()
		if self.keylog: 
			self.keylog.close()
			self.keylog = None
	def service(self):
		pythoncom.PumpWaitingMessages()

		if self.keylog:
			for t,event in self.events:
				key = event.GetKey()
				if event.Transition == 0 and event.GetKey() in self.pressed:
					continue
				elif event.Transition == 0:
					self.pressed.append(key)					
				elif event.Transition > 0:
					if key in self.pressed:
						self.pressed.remove(key)
				self.keylog.write("{:.2f},{},{}\n".format(t - self.start_time, 'p' if event.Transition == 0 else 'r', key))
				self.keylog.flush()
			self.events = []
		