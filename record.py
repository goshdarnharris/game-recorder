import capture
import keylogger
import mask_test
import pyaudio, wave, glob
import time, os, concurrent.futures, multiprocessing, threading
from collections import Iterator
from PIL import Image

class RateLimiter(Iterator):
    """Iterator that yields a value at most once every 'interval' seconds.
    From user Gareth Rees on Stack Overflow."""
    def __init__(self, interval):
        self.lock = threading.Lock()
        self.interval = interval
        self.next_yield = 0

    def __next__(self):
        with self.lock:
            t = time.monotonic()
            if t < self.next_yield:
                time.sleep(self.next_yield - t)
                t = time.monotonic()
            self.next_yield = t + self.interval

class Channel(object):
	def __init__(self, in_queue = None, out_queue = None):
		self.__in_queue = in_queue or multiprocessing.Queue()
		self.__out_queue = out_queue or multiprocessing.Queue()
	def flip(self):
		return type(self)(in_queue = self.__out_queue, out_queue = self.__in_queue)
	def send(self, event, *args):
		self.__out_queue.put((event, args))
	def recv(self, blocking = True):
		if blocking or not self.__in_queue.empty():
			return self.__in_queue.get()
		return None
	def poke(self):
		return self.__in_queue.empty()

class FrameLogger(object):
	def __init__(self, interval):
		self.min_frame_interval = interval
		self.limiter = RateLimiter(self.min_frame_interval)
		self.last_time = 0
		self.frame_count = 0
		self.frames = 0
		self.start_time = 0
		self.logging = False
		self.directory = None
	def start(self, window, start_time, directory):
		if not self.logging:
			self.frames = 0
			self.start_time = start_time
			self.directory = directory
			self.window = window
			self.logging = True
	def stop(self):
		self.logging = False
	def service(self):
		if self.logging:
			next(self.limiter)

			if time.monotonic() - self.last_time >= 1:
				self.last_time = time.monotonic()
				print(self.frame_count)
				self.frame_count = 0

			filename = "{:.2f}-{:03d}-{:d}.jpg".format(time.monotonic() - self.start_time, self.frame_count, self.frames)
			capture.frame(self.window, os.path.join(self.directory, filename))
			self.frames += 1
			self.frame_count += 1

class AudioLogger(object):
	def __init__(self):
		self.pyaudio = pyaudio.PyAudio()
		self.ostream = None
		self.istream = None
	def start(self, window, start_time, directory):
		if not self.istream and not self.ostream:
			device = self.pyaudio.get_default_output_device_info()
			self.istream = self.pyaudio.open(format = pyaudio.paInt16,
				channels = device["maxOutputChannels"],
				rate = int(device["defaultSampleRate"]),
				input = True,
				frames_per_buffer = 512,
				input_device_index = device["index"],
				as_loopback = True
			)

			self.ostream = wave.open(os.path.join(directory, "audio.wav"), 'wb')
			self.ostream.setnchannels(device["maxOutputChannels"])
			self.ostream.setsampwidth(self.pyaudio.get_sample_size(pyaudio.paInt16))
			self.ostream.setframerate(int(device["defaultSampleRate"]))
	def stop(self):
		self.ostream.close()
		self.istream.close()
		self.ostream = None
		self.istream = None
	def service(self):
		if self.ostream and self.istream:		
			if self.istream.get_read_available() >= 512:
				self.ostream.writeframes(self.istream.read(512))
			else:
				time.sleep(0.01)

class FrameDetector(object):
	def __init__(self, interval, test_config):
		self.tests = {}
		self.running = False
		self.window = None
		self.limiter = RateLimiter(interval)
		self.last_event = None
		self.test_config = test_config
	def start(self, window):
		self.window = window
		self.tests = {}
		for event,config in self.test_config.items():
			window_size = capture.get_window_size(self.window)
			image_size = (window_size[2], window_size[3])
			target = Image.open(config[0]).convert("RGB").resize(image_size)
			mask = Image.open(config[1]).convert("RGB").resize(image_size)
			threshold = config[2]
			self.tests[event] = mask_test.create_test(target, mask, threshold)
		self.running = True		
	def stop(self):
		self.running = False
	def service(self):
		if self.running and capture.window_exists(self.window):
			next(self.limiter)
			for event,test in self.tests.items():
				frame = capture.frame(self.window)
				frame = Image.frombytes('RGB', frame.size, frame.rgb)
				if test(frame):
					if self.last_event == event:
						return					
					self.last_event = event
					return event
			self.last_event = None

def capture_process(config):
	try:
		process_name = config.logger_class.__name__
		print("Starting", process_name)
		logger = config.logger_class(*config.args)
		while True:
			if config.remote.poll():
				event,args = config.remote.recv()
				if event == "start":
					print(process_name, "got start event")
					logger.start(*args)
				if event == "stop":
					print(process_name, "got stop event")
					logger.stop()
				if event == "terminate":
					print(process_name, "is terminating.")
					logger.stop()
					return
			ret = logger.service()
			if ret is not None:
				config.remote.send(ret)
	finally:
		return

class CaptureConfig(object):
	def __init__(self, logger_class, *args):
		self.local,self.remote = multiprocessing.Pipe()
		self.args = args
		self.logger_class = logger_class

class Recorder(object):
	def __init__(self, window, min_frame_interval = .1, frame_test_interval = .5):
		self.window = window
		self.min_frame_interval = min_frame_interval
		self.frame_test_interval = frame_test_interval
		self.window_exists = False
		self.logging = False

		#Load up frame detection images
		mask_dir = os.path.join("state_mask", window)
		detection = {}
		for filename in glob.glob(os.path.join(mask_dir, "*.png")):
			filename = os.path.split(filename)[-1]
			if "_mask.png" not in filename:				
				event,ext = os.path.splitext(filename)
				detection[event] = [os.path.join(mask_dir, filename), os.path.join(mask_dir, event+"_mask"+ext), 10]

		self.logger_configs = [
			CaptureConfig(FrameLogger, self.min_frame_interval),
			CaptureConfig(AudioLogger),
			CaptureConfig(keylogger.KeyLogger)
		]

		self.detector_configs = [
			CaptureConfig(FrameDetector, self.frame_test_interval, detection)
		]
		self.pool = multiprocessing.Pool(len(self.logger_configs) + len(self.detector_configs))
	def dispatch_loggers(self, event, *args):
		for config in self.logger_configs:
			config.local.send((event, args))
	def dispatch_detectors(self, event, *args):
		for config in self.detector_configs:
			config.local.send((event, args))
	def receive_loggers(self):
		for config in self.logger_configs:
			if config.local.poll():
				return config.local.recv()
	def receive_detectors(self):
		for config in self.detector_configs:
			if config.local.poll():
				return config.local.recv()
	def start(self):
		self.pool.map_async(capture_process, self.logger_configs + self.detector_configs)
	def service(self):
		if not capture.window_exists(self.window) and self.window_exists:
			print("Game window closed. Stopping detectors & loggers.")
			self.dispatch_detectors("stop")
			if self.logging:
				self.dispatch_loggers("stop")
			self.window_exists = False
		if capture.window_exists(self.window) and not self.window_exists:
			print("Game window detected. Starting detectors.")
			self.dispatch_detectors("start", self.window)
			self.window_exists = True
		if self.window_exists:
			event = self.receive_detectors()
			if event == "start" and not self.logging:
				print("Recorder detected start event. Starting logging.")
				directory = time.strftime("%m-%d-%y-%H-%M-%S")
				os.makedirs(directory, exist_ok = True)
				self.dispatch_loggers("start", self.window, time.monotonic(), directory)
				self.logging = True
			if event == "stop" and self.logging:
				print("Recorder detected stop event. Stopping logging.")
				self.dispatch_loggers("stop")
				self.logging = False
		time.sleep(.1)
	def stop(self):
		self.dispatch_loggers("terminate")
		self.dispatch_detectors("terminate")

		while self.receive_loggers(): pass
		while self.receive_detectors(): pass

		time.sleep(.5)
		self.pool.terminate()
		

if __name__ == "__main__":
	recorder = Recorder("Spelunky", min_frame_interval = .1, frame_test_interval = .1)

	try:
		recorder.start()
		while True:
			recorder.service()
	except KeyboardInterrupt:
		print("Terminating...")
		recorder.stop()

			