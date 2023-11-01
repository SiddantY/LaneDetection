import time
import math
import numpy as np
import cv2
import rospy

from line_fit import line_fit, tune_fit, bird_fit, final_viz
from Line import Line
from sensor_msgs.msg import Image
from std_msgs.msg import Header
from cv_bridge import CvBridge, CvBridgeError
from std_msgs.msg import Float32
from skimage import morphology



class lanenet_detector():
    def __init__(self):

        self.bridge = CvBridge()
        # NOTE
        # Uncomment this line for lane detection of GEM car in Gazebo
        self.sub_image = rospy.Subscriber('/gem/front_single_camera/front_single_camera/image_raw', Image, self.img_callback, queue_size=1)
        # Uncomment this line for lane detection of videos in rosbag
        # self.sub_image = rospy.Subscriber('camera/image_raw', Image, self.img_callback, queue_size=1)
        self.pub_image = rospy.Publisher("lane_detection/annotate", Image, queue_size=1)
        self.pub_bird = rospy.Publisher("lane_detection/birdseye", Image, queue_size=1)
        self.left_line = Line(n=5)
        self.right_line = Line(n=5)
        self.detected = False
        self.hist = True


    def img_callback(self, data):

        try:
            # Convert a ROS image message into an OpenCV image
            cv_image = self.bridge.imgmsg_to_cv2(data, "bgr8")
        except CvBridgeError as e:
            print(e)

        raw_img = cv_image.copy()
        mask_image, bird_image = self.detection(raw_img)

        if mask_image is not None and bird_image is not None:
            # Convert an OpenCV image into a ROS image message
            out_img_msg = self.bridge.cv2_to_imgmsg(mask_image, 'bgr8')
            out_bird_msg = self.bridge.cv2_to_imgmsg(bird_image, 'bgr8')
            # Publish image message in ROS
            self.pub_image.publish(out_img_msg)
            self.pub_bird.publish(out_bird_msg)


    def gradient_thresh(self, img, thresh_min=30, thresh_max=255):
        """
        Apply sobel edge detection on input image in x, y direction
        """
        ## TODO
        # 1. Convert the image to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 2. Gaussian blur the image
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        #3. Use cv2.Sobel() to find derivatives for both X and Y Axis
        sobelx = cv2.Sobel(blurred, cv2.CV_64F, 1, 0, ksize=3)
        sobely = cv2.Sobel(blurred, cv2.CV_64F, 0, 1, ksize=3)
        # 4. Use cv2.addWeighted() to combine the results
        abs_sobelx = np.absolute(sobelx)
        abs_sobely = np.absolute(sobely)
        combined_sobel = cv2.addWeighted(abs_sobelx, 1, abs_sobely, 1, 0)
        # 5. Convert each pixel to uint8, then apply threshold to get a binary image
        scaled_sobel = np.uint8(255 * combined_sobel / np.max(combined_sobel))
        # sob_output[(scaled_sobel >= thresh_min) & (scaled_sobel <= thresh_max)] = 255
        ret, sob_output = cv2.threshold(scaled_sobel, thresh_min, thresh_max, cv2.THRESH_BINARY)

        return sob_output


    def color_thresh(self, img, thresh=(100, 255)):
        """
        Convert RGB to HSL and threshold to binary image using S channel
        """
        #Hint: threshold on H to remove green grass
        ## TODO
        #1. Convert the image from RGB to HSL
        col = cv2.cvtColor(img, cv2.COLOR_BGR2HLS)
        #2. Apply threshold on S channel to get binary image
        s_channel = col[:, :, 2]
        # ret, binary_output = cv2.threshold(s_channel, thresh[0], thresh[1], cv2.THRESH_BINARY)
        ret, binary_output = cv2.threshold(s_channel, thresh[0], thresh[1], cv2.THRESH_BINARY)
        return binary_output
      

    def combinedBinaryImage(self, img):
        """
        Get combined binary image from color filter and sobel filter
        """
        ## Here you can use as many methods as you want.
        ## TODO
        #1. Apply sobel filter and color filter on input image
        SobelOutput = self.gradient_thresh(img)
        ColorOutput = self.color_thresh(img)
        #2. Combine the outputs
        binaryImage = np.zeros_like(SobelOutput)
        binaryImage[(ColorOutput==255)|(SobelOutput==255)] = 255
        # Remove noise from binary image
        # bag 0011
        # binaryImage = morphology.remove_small_objects(binaryImage.astype('bool'),min_size=0,connectivity=2)
        # bag 0056
        # binaryImage = morphology.remove_small_objects(binaryImage.astype('bool'),min_size=0,connectivity=2)

        return binaryImage


    def perspective_transform(self, img, verbose=False):
        """
        Get bird's eye view from input image
        """
        ## TODO

        img = img.astype('uint8') * 255
        
        #1. Visually determine 4 source points and 4 destination points
        img_size = (img.shape[1], img.shape[0])
        # print(IMAGE_H, IMAGE_W)
        # sim
        # src = np.float32([[240, 280], [400, 275], [65, 380], [610, 380]])
        #bag 0011:
        src = np.float32([[500, 254], [750, 254], [200, 375], [800, 375]])
        #bag 0056:
        # src = np.float32([[450, 250], [700, 250], [200, 360], [900, 360]])

        #2. Get M, the transform matrix, and Minv, the inverse using cv2.getPerspectiveTransform()
        dst = np.float32([[0, 0], [img.shape[1], 0], [0, img.shape[0]], [img.shape[1], img.shape[0]]])
        M = cv2.getPerspectiveTransform(src, dst) # The transformation matrix
        Minv = cv2.getPerspectiveTransform(dst, src) # Inverse transformation

        #3. Generate warped image in bird view using cv2.warpPerspective()
        warped_img = cv2.warpPerspective(img,M,img_size)


        return warped_img, M, Minv


    def detection(self, img):

        binary_img = self.combinedBinaryImage(img)
        img_birdeye, M, Minv = self.perspective_transform(binary_img)

        if not self.hist:
            # Fit lane without previous result
            ret = line_fit(img_birdeye)
            left_fit = ret['left_fit']
            right_fit = ret['right_fit']
            nonzerox = ret['nonzerox']
            nonzeroy = ret['nonzeroy']
            left_lane_inds = ret['left_lane_inds']
            right_lane_inds = ret['right_lane_inds']

        else:
            # Fit lane with previous result
            if not self.detected:
                ret = line_fit(img_birdeye)

                if ret is not None:
                    left_fit = ret['left_fit']
                    right_fit = ret['right_fit']
                    nonzerox = ret['nonzerox']
                    nonzeroy = ret['nonzeroy']
                    left_lane_inds = ret['left_lane_inds']
                    right_lane_inds = ret['right_lane_inds']

                    left_fit = self.left_line.add_fit(left_fit)
                    right_fit = self.right_line.add_fit(right_fit)

                    self.detected = True

            else:
                left_fit = self.left_line.get_fit()
                right_fit = self.right_line.get_fit()
                ret = tune_fit(img_birdeye, left_fit, right_fit)

                if ret is not None:
                    left_fit = ret['left_fit']
                    right_fit = ret['right_fit']
                    nonzerox = ret['nonzerox']
                    nonzeroy = ret['nonzeroy']
                    left_lane_inds = ret['left_lane_inds']
                    right_lane_inds = ret['right_lane_inds']

                    left_fit = self.left_line.add_fit(left_fit)
                    right_fit = self.right_line.add_fit(right_fit)

                else:
                    self.detected = False

            # Annotate original image
            bird_fit_img = None
            combine_fit_img = None
            if ret is not None:
                bird_fit_img = bird_fit(img_birdeye, ret, save_file=None)
                combine_fit_img = final_viz(img, left_fit, right_fit, Minv)
            else:
                print("Unable to detect lanes")

            return combine_fit_img, bird_fit_img


if __name__ == '__main__':
    # init args
    rospy.init_node('lanenet_node', anonymous=True)
    lanenet_detector()
    while not rospy.core.is_shutdown():
        rospy.rostime.wallsleep(0.5)
