#!/usr/bin/env python

import numpy as np
import rospy as ros
from sensor_msgs.msg import Image
from sensor_msgs.msg import CameraInfo
import cv2
import cv2.aruco as aruco
from cv_bridge import CvBridge, CvBridgeError
import roslib
import tf
import yaml
import math

class ArucoDetector(object):

    def __init__(self):
        ''' Initialize the detector '''

        # Initialize ROS node
        ros.init_node("aruco_detector", anonymous=False, log_level=ros.DEBUG)
        self.bridge = CvBridge()
        self.rate = ros.Rate(90) # in hz

        # Initialize ROS Subscriber
        try:
            image_topic = ros.get_param('~image_topic')
        except:
            ros.logerr("Image topic not set in launch file!")
            exit()
        ros.logdebug("Image topic loaded..")
        self.sub = ros.Subscriber(image_topic, Image, self.cbDetect)

        # Initialize tf broadcaster/listener and first tag check
        self.br = tf.TransformBroadcaster()
        self.ls = tf.TransformListener()
        self.initial_id = None

        # Grab camera info
        try:
            ros.logdebug("Checking for camera info...")
            camera_info_topic = ros.get_param('~camera_info_topic')
        except:
            ros.logerr("Camera_info topic not set in launch file! Searching for\
             calibration file!")
            try:
                self.loadCalibration()
            except:
                ros.logerr("No calibration file found!")
                exit()

        ##DEBUG: Need to figure out camera_info_topic, below is placeholder
        self.loadCalibration()

        # Configure aruco detector
        self.aruco_dict = aruco.Dictionary_get(aruco.DICT_6X6_250)
        self.aruco_params = aruco.DetectorParameters_create()

        # Initialize database of id and world coordinates
        self.id_db = dict()

        # Keep track of rvecs and tvecs to use in drawing the axes
        self.tvecs = [0]
        self.rvecs = [0]

    def loadCalibration(self):

        ''' Load the camera calibration file and read relevant values'''
        with open(
        '/home/nuc/catkin_ws/src/zayas_test/scripts/ost.yaml','r') as f:
            doc = yaml.load(f)
        self.matrix = np.asarray(doc["camera_matrix"]["data"])
        self.matrix = self.matrix.reshape(3,3)
        self.dist = np.asarray(doc["distortion_coefficients"]["data"])
        ros.loginfo(self.matrix)
        ros.loginfo(self.dist)

    ##TODO: Double check how to work with camera_info_topic through ROS
    def cbCamInfo(self, msg):
        '''Retrieve cam info data from message'''

        self.matrix = msg.K
        self.dist = msg.D
        ros.loginfo(self.matrix)

    def extractImage(self, msg):
        ''' Extracts data from msg and converts to usable numpy array'''

        try:
            image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            img = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except CvBridgeError as e:
            print(e)
        return img

    def detectMarkers(self, frame):
        ''' Detects markers based on default params. '''

        aruco_params = aruco.DetectorParameters_create()
        corners, ids, rejectedImgPoints = aruco.detectMarkers(
            frame, self.aruco_dict, parameters = aruco_params)

        return corners, ids

    def obtainPose(self, corners, ids):
        ''' Retrieves pose from given markers and (for now) handles tf stuff.
        Runs once per frame received.'''

        ##TODO: Split tf and pose extraction
        # This method is super gross right now and you should be embarassed.
        rvecs, tvecs, _objPoints = aruco.estimatePoseSingleMarkers(
            corners, .185, self.matrix, self.dist)
        self.rvecs = rvecs
        self.tvecs = tvecs

        ''' If localization hasn't started and a tag is detected, make that
         first tag the 'origin' of our world. When we code for the cornfield
         we can define the exact position of an aruco tag and map estimations
         of the rest and possibly correct for it.'''

        # LOG OUR INITIAL ID
        if (self.initial_id == None):
            self.initial_id = ids[0][0]
            self.id_db[self.initial_id] = [
                (0,0,0), tf.transformations.quaternion_from_euler(0,0,0)]

        # LOOP THROUGH ALL DETECTED IDS
        for i in range(len(ids)):
            ##TODO: Pretty much this entire loop should be handled by a db node

##########################THIS PART IS BAD######################################

            # UPDATE OUR CAMERA POSITION BASED ON TAGS
            ros.loginfo("RVECS BEFORE RODRIGUES: " + str(rvecs))
            rvec = cv2.Rodrigues(rvecs[i])
            ros.loginfo("RVEC AFTER RODRIGUES: " + str(rvec))
            #rvec = self.rotationMatrixToEulerAngles(rvec[0])
            M = np.identity(4)
            M[:3, :3] = rvec[0]
            rvec = M
            rvec = tf.transformations.quaternion_from_matrix(rvec)
            ros.loginfo("RVEC AFTER TF.TRANSFORMATIONS: " + str(rvec))
            self.br.sendTransform(
                tvecs[i][0], rvec, ros.Time.now(), "/" + str(ids[i][0]),
                "/camera")

            # IF THE ID ISNT IN THE DATABASE AND WE HAVE ANOTHER TAG FOR REFERENCE... FIND THE TRANSFORM AND PUT IN DATABASE
            # TODO ONLY CONSIDER IF ONE OF THE OTHER TAGS ARE LOCALIZED!!!
            if ((ids[i][0] not in self.id_db) and len(ids) > 1):
                # Rodrigues -> Euler
                rvec = cv2.Rodrigues(rvecs[i][0])
                rvec = self.rotationMatrixToEulerAngles(rvec[0])

                self.br.sendTransform(tvecs[i][0],tf.transformations.quaternion_from_euler(rvec[0],rvec[1],rvec[2]), ros.Time.now(), "/" + str(ids[i][0]), "/camera")
                trans, rot = self.ls.lookupTransform('/' + str(ids[i][0]), '/world', ros.Time())
                ros.logdebug(trans)
                ros.logdebug(rot)
                self.id_db[ids[i][0]] = [trans, rot]

#################################################################################

        for key in self.id_db:

            # Split dict entry
            w_tvec, w_rvec = self.id_db[key]

            # Now Broadcast
            self.br.sendTransform(
            w_tvec, w_rvec, ros.Time.now(), "/" + str(key), "/world")

        ##DEBUG: Debug to print the database + world anchors
        ros.logdebug("id_db: " + str(self.id_db))
        ros.logdebug("========================================\n")

        return rvecs, tvecs

###THE FOLLOWING TWO FUNCTIONS ARE FROM#########################################
#https://www.learnopencv.com/rotation-matrix-to-euler-angles/###################

    # Checks if a matrix is a valid rotation matrix.
    def isRotationMatrix(self, R) :
        Rt = np.transpose(R)
        shouldBeIdentity = np.dot(Rt, R)
        I = np.identity(3, dtype = R.dtype)
        n = np.linalg.norm(I - shouldBeIdentity)
        return n < 1e-6


    # Calculates rotation matrix to euler angles
    def rotationMatrixToEulerAngles(self, R) :

        assert(self.isRotationMatrix(R))

        sy = math.sqrt(R[0,0] * R[0,0] +  R[1,0] * R[1,0])

        singular = sy < 1e-6

        if  not singular :
            x = math.atan2(R[2,1] , R[2,2])
            y = math.atan2(-R[2,0], sy)
            z = math.atan2(R[1,0], R[0,0])
        else :
            x = math.atan2(-R[1,2], R[1,1])
            y = math.atan2(-R[2,0], sy)
            z = 0

        return np.array([x, y, z])

################################################################################

    def drawMarkers(self, corners, frame):
        ''' Draws the detected markers on the frame. '''
        debugFrame = aruco.drawDetectedMarkers(frame, corners)

        #DEBUG: Draw axes. This is currently very shaky and inaccurate.. :(..
        #if (True):
        #    ros.loginfo("rvecs: " + str(self.rvecs))
        #    debugFrame = aruco.drawAxis(
        #    debugFrame, self.matrix, self.dist, self.rvecs[0],
        #     self.tvecs[0], 100)

        cv2.imshow('DEBUG', debugFrame)

    def cbDetect(self, msg):
        ''' The main callback loop, run whenever a frame is received. '''

        frame = self.extractImage(msg)

        corners, ids = self.detectMarkers(frame)

        if (len(corners)!=0): # If we detect corners...
            rvecs, tvecs = self.obtainPose(corners, ids)
            self.drawMarkers(corners,frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            ros.logfatal("Quitting...")



if __name__ == '__main__':
    ros.logdebug("Creating Aruco Detector node...")
    ad = ArucoDetector()
    ros.logdebug("Aruco Detector initialized. Now spinning.")
    ros.spin()