from PIL import Image, ImageChops, ImageStat
import os, glob

def create_test(target, mask, threshold):
	match_image = ImageChops.multiply(target, mask)
	def test_frame(frame):
		masked = ImageChops.multiply(frame, mask)
		result = ImageChops.difference(masked, match_image)
		return sum(ImageStat.Stat(result).rms) < threshold
	return test_frame

