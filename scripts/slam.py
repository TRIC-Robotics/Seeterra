#!/usr/bin/env python

import tf
import atexit
import math
import yaml
import sys
import rospy
import numpy as np
from geometry_msgs.msg import *
from sensor_msgs.msg import Image
from sensor_msgs.msg import CameraInfo
from fiducial_msgs.msg import *
from tf import transformations as t

class Slam:

	def __init__(self, setup_mode=True, rate=90):
		""" Initialize slam node """

		rospy.init_node("slam", anonymous=False, log_level=rospy.DEBUG)
		#####################TEMP: VICON ERROR ANALYSIS#########################
		self.tag_sub = rospy.Subscriber("/vicon/tag_1/tag_1", TransformStamped, self.cb_test)
		self.cam_sub = rospy.Subscriber("/vicon/cam_1/cam_1", TransformStamped, self.cb_test)
		########################################################################
		self.sub = rospy.Subscriber("/fiducial_transforms", FiducialTransformArray, self.cb)
		self.rate = rospy.Rate(rate) # in hz
		self.br = tf.TransformBroadcaster()
		self.ls = tf.TransformListener()
		self.temp_position = []
		#TEMP: There's a better solution for this, but prototype for now
		self.temp_1 = []
		self.temp_2 = []
		self.temp_3 = []
		self.temp_db = {1: None,
						2: None,
						3: None}

		self.id_db = dict()
		if setup_mode:
			rospy.logdebug("SETUP MODE")
		else:
			rospy.logdebug("NAVIGATION MODE")
			self.loadTagConfiguration()
			rospy.logdebug(self.id_db)

		self.position = Transform()
		self.tracking = False

	def addTransforms(self, frame1, frame2):
		""" Adds two transform objects to get a resulting transform. """

		t1 = np.array([frame1.translation.x,frame1.translation.y,frame1.translation.z])
		t2 = np.array([frame2.translation.x,frame2.translation.y,frame2.translation.z])

		r1 = np.array([frame1.rotation.x, frame1.rotation.y, frame1.rotation.z, frame1.rotation.w])
		r2 = np.array([frame2.rotation.x, frame2.rotation.y, frame2.rotation.z, frame2.rotation.w])

		f1 = t.compose_matrix(translate=t1,angles=t.euler_from_quaternion(r1))
		f2 = t.compose_matrix(translate=t2,angles=t.euler_from_quaternion(r2))

		f = np.matmul(f1,f2)

		trans = t.translation_from_matrix(f)
		rot = t.quaternion_from_matrix(f)

		trans = Vector3(trans[0],trans[1],trans[2])
		rot = Quaternion(rot[0],rot[1],rot[2],rot[3])

		return Transform(trans, rot)

	def arrayify(self, n):
		""" Turn the transform into two nparrays, translation and rotation """

		trans = np.array([n.translation.x, n.translation.y, n.translation.z])
		rot = np.array([ n.rotation.x, n.rotation.y, n.rotation.z, n.rotation.w])

		return trans, rot

	def cb(self, msg):
		""" Callback loop """
		#TODO: Split callback for readability

		# If we have a database, check tags against it
		if self.id_db:
			self.errorCheck(msg)

		#    If id_db exists   if we have a message
		if (not self.id_db) and msg.transforms:
			self.startDatabase(msg.transforms[0].fiducial_id)

		for i in msg.transforms:
			if i.fiducial_id in self.id_db: # ..THEN UPDATE POSITION
				if len(msg.transforms) >= 2:
					if self.tagCheck(i):
						pass
						print("PASSED")
					else:
						continue
					print("TRACKING")
					self.tracking = True
					self.updatePosition(i)
				else:
					self.tracking = True
					self.updatePosition(i)
			else: # UPDATE DATABASE
				if self.tracking:
					world_to_tag = self.addTransforms(self.position,i.transform)
					translation, rotation = self.arrayify(world_to_tag)
					self.updateDatabase(i,translation,rotation)

		for key in self.id_db:
			t,r = self.arrayify(self.id_db[key])
			self.br.sendTransform(t,r,rospy.Time.now(), "/tags/" + str(key), "/world")

		self.tracking = False

	def cb_test(self, msg):
		""" Temporary function for VICON position error analysis """

		vicon_position = np.array([msg.transform.translation.x,
								msg.transform.translation.y,
								msg.transform.translation.z])

		print(vicon_position)

	def errorCheck(self, msg):
		""" Uses the camera's current position as well as any detected tags
		to check against the database and determine whether there is an error."""

		# Camera's world transform (from tf)
		try:
			t, r = self.ls.lookupTransform('/camera', '/world', rospy.Time())
			t = Vector3(t[0],t[1],t[2])
			r = Quaternion(r[0],r[1],r[2],r[3])
			camera_position = Transform(t,r)
		except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException), e:
			rospy.logwarn("Camera not found in lookup!")
			rospy.logwarn(str(e))
			return 0

	def inverseTransform(self, input_transform):

		translation, rotation = self.arrayify(input_transform)

		transform = t.compose_matrix(translate=translation,angles=t.euler_from_quaternion(rotation))
		inversed_transform = t.inverse_matrix(transform)

		translation = t.translation_from_matrix(inversed_transform)
		quaternion = t.quaternion_from_matrix(inversed_transform)

		final_transform = self.transformify(translation, quaternion)

		return final_transform

	def loadTagConfiguration(self):
		""" Load tag configuration from yaml file """

		rospy.logdebug("Loading tag configuration")
		with open('/home/nuc/catkin_ws/src/navigation_stack/config/id_db.yml', 'r') as yaml_file:
			self.id_db = yaml.load(yaml_file)

	def reject_outliers(data, m = 2.):
		""" Rejects Outliers
		borrowed from https://stackoverflow.com/questions/11686720/is-there-a-numpy-builtin-to-reject-outliers-from-a-list """

		d = np.abs(data - np.median(data))
		mdev = np.median(d)
		s = d/mdev if mdev else 0.
		return data[s<m]

	def saveTagConfiguration(self):
		""" Save tag configuration to yaml file """

		rospy.loginfo("Saving tag configuration..")
		#TEMP: Temporary file location, link dynamically
		with open('/home/nuc/catkin_ws/src/navigation_stack/config/id_db.yml', 'w') as yaml_file:
			yaml.dump(self.id_db, yaml_file)

	def startDatabase(self, id):
		""" Populate database with initial tag """

		init = Transform()
		init.rotation.w = 1

		self.id_db[id] = init

		tags = []
		for tag in msg.transforms:
			if tag.fiducial_id in self.id_db:
				tags.append(tag)

	def tagCheck(self, tag, translational_error=.1, rotational_error=.261799):
		""" Checks whether the tag is where we expect it to be """

		#DEBUG:
		print self.addTransforms(tag.transform,self.inverseTransform(tag.transform))

		tag_from_db = self.id_db[tag.fiducial_id]
		tag_from_camera = self.addTransforms(self.position, tag.transform)

		translation_difference, rotation_difference = self.arrayify(self.addTransforms(tag_from_db,self.inverseTransform(tag_from_camera)))
		rotation_difference = t.euler_from_quaternion(rotation_difference)

		error = False

		# Position Error
		for i in translation_difference:
			if abs(i) >= translational_error:
				error = True

		# Rotational Error
		for i in rotation_difference:
			if abs(i) >= rotational_error:
				error = True

		# DEBUG:
		rospy.loginfo("=======================================================")
		rospy.loginfo("TAG: " + str(tag.fiducial_id))
		rospy.loginfo("T_DIFF: " + str(translation_difference))
		rospy.loginfo("R_DIFF: " + str(rotation_difference))

		if error:
			rospy.logwarn("TAG ERROR")
			return False
		else:
			return True

	def transformify(self, t, r):
		""" Turns a translation and rotation to a transform object """

		translation = Vector3(t[0],t[1],t[2])
		rotation= Quaternion(r[0],r[1],r[2],r[3])
		transform = Transform(translation, rotation)

		return transform

	def updateDatabase(self, tag, t, r, samples = 15):
		""" Grab a number of samples then average and add to database """
		#TODO: There's gotta be a better way to do this, maybe tag_db node

		rospy.logdebug(tag.fiducial_id)

		if not len(self.temp_1) or self.temp_db[1] == tag.fiducial_id:
			rospy.logdebug("LOGGING SAMPLE IN TEMP_1")
			self.temp_1.append(np.array((t,r)))
			self.temp_db[1] = tag.fiducial_id
		elif not len(self.temp_2) or self.temp_db[2] == tag.fiducial_id:
			rospy.logdebug("LOGGING SAMPLE IN TEMP_2")
			self.temp_2.append(np.array((t,r)))
			self.temp_db[2] = tag.fiducial_id
		elif not len(self.temp_3) or self.temp_db[3] == tag.fiducial_id:
			rospy.logdebug("LOGGING SAMPLE IN TEMP_3")
			self.temp_3.append(np.array((t,r)))
			self.temp_db[3] = tag.fiducial_id
		else:
			rospy.logwarn("All temporary arrays in use.")

		if len(self.temp_1) == samples:
			rospy.logdebug("AVERAGING TEMP_1")
			#temp_1 = self.reject_outliers(self.temp_1)
			#rospy.logwarn(temp_1)
			avg = np.average(self.temp_1, axis=0)
			tag_transform = self.transformify(avg[0],avg[1])
			self.temp_1 = []
			self.temp_db[1] = None
			self.id_db[tag.fiducial_id] = tag_transform
			rospy.logdebug("TEMP_1 DUMPED")
		elif len(self.temp_2) == samples:
			rospy.logdebug("AVERAGING TEMP_2")
			#temp_2 = self.reject_outliers(self.temp_2)
			#rospy.logwarn(temp_2)
			avg = np.average(self.temp_2, axis=0)
			tag_transform = self.transformify(avg[0],avg[1])
			self.temp_2 = []
			self.temp_db[2] = None
			self.id_db[tag.fiducial_id] = tag_transform
			rospy.logdebug("TEMP_2 DUMPED")
		elif len(self.temp_3) == samples:
			rospy.logdebug("AVERAGING TEMP_3")
			#temp_3 = self.reject_outliers(self.temp_3)
			#rospy.logwarn(temp_3)
			avg = np.average(self.temp_3, axis=0)
			tag_transform = self.transformify(avg[0],avg[1])
			self.temp_3 = []
			self.temp_db[3] = None
			self.id_db[tag.fiducial_id] = tag_transform
			rospy.logdebug("TEMP_3 DUMPED")
		else:
			return

	def updatePosition(self, tag, samples = 20):
		""" Update the camera's position """

		inverse_transform = self.inverseTransform(tag.transform)
		self.position = self.addTransforms(self.id_db[tag.fiducial_id],
											inverse_transform)
		translation, rotation = self.arrayify(self.position)

		# Build samples before publishing
		if self.temp_position <= samples:
			self.temp_position.append([translation,rotation])
		else:
			avg = np.average(self.temp_position, axis=0)
			self.br.sendTransform(translation, rotation, rospy.Time.now(), "/camera", "/world")
			self.temp_position = self.temp_position[1:]
			self.temp_position.append([translation,rotation])

if __name__== "__main__":

	try:
		if sys.argv[1].lower() == "true":
			setup_mode = True
		elif sys.argv[1].lower() == "false":
			setup_mode = False
		else:
			rospy.logerror("Please enter True or False for the first argument (setup_mode)")
			exit()
	except:
		setup_mode = False

	slam = Slam(setup_mode)

	if setup_mode == True:
		atexit.register(slam.saveTagConfiguration)

	rospy.spin()
