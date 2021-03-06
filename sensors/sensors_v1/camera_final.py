import pyzed.sl as sl
from abc import ABC, abstractmethod
import numpy as np
import time
import cv2
from datetime import datetime
from threading import Thread, Lock
import os

##################################################################
# EXCEPTIONS
##################################################################


class CameraActivationError(Exception):
    '''
    Exception Called When:
    1. Zed Camera Fails to Open -- try unplugging and replugging in camera
    '''
    pass


class ImageCaptureError(Exception):
    '''
    Exception Called When:
    1. We fail to receive an image from the camera when zed.grab() is called
    '''
    pass


##################################################################
# ABSTRACT BASE CLASS
##################################################################


class CameraSensor(ABC):

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def get_image_frame(self):
        pass

    @abstractmethod
    def _update(self):
        pass

    @abstractmethod
    def exit(self):
        pass

##################################################################
# CAMERA SENSOR CLASS
##################################################################


class ZedCameraSensor(CameraSensor):
    """
    DOC STRING
    """

    def __init__(self,
                 camera_resolution: str = '1080',
                 fps: int = 30,
                 camera_view: str = 'left',
                 include_depth: bool = True):
        # **** Write a small comment for each variable
        # ** so anyone can know what each one does later on

        self.camera_info = {
            '720': {
                'fps': [15, 30, 60]
            },

            '1080': {
                'fps': [15, 30]
            },

            '2K': {
                'fps': [15]
            },
        }

        self.zed = None
        self.raw_zed_data = None
        self.image_frame = None
        self.include_depth = include_depth
        self.raw_depth_data = None
        self.depth_map = None

        self.started = None
        self.read_lock = Lock()

        assert camera_view.lower() in ['left', 'right'], 'Incorrect Camera View'
        assert camera_resolution in self.camera_info.keys(), 'Incorrect Resolution'
        assert fps in self.camera_info[camera_resolution]['fps'], 'Invalid FPS for given resolution'

        self.camera_resolution = camera_resolution
        self.fps = fps
        self.camera_view = camera_view.lower()

        # image size parameters
        self.image_width = None
        self.image_height = None
        self.num_channels = 4

    def start(self) -> None:
        """
        This function is used to initialize and start the camera
        Once started it continuously updates image/depth map information
        :return: None
        """

        if self.started:
            print('[info] camera sensor already started')
            return None

        # define camera system
        self.zed = sl.Camera()

        # set camera config parameters
        init_params = sl.InitParameters()

        # select resolution
        if self.camera_resolution == '720':
            init_params.camera_resolution = sl.RESOLUTION.HD720
        elif self.camera_resolution == '1080':
            init_params.camera_resolution = sl.RESOLUTION.HD1080
        else:
            init_params.camera_resolution = sl.RESOLUTION.HD2K

        # select frames per second
        init_params.camera_fps = self.fps

        # set camera
        if self.camera_view == 'left':
            self.camera_view = sl.VIEW.LEFT
        else:
            self.camera_view = sl.VIEW.RIGHT

        # if depth sensing is specified
        if self.include_depth:
            init_params.depth_mode = sl.DEPTH_MODE.ULTRA
            init_params.coordinate_units = sl.UNIT.MILLIMETER

        # initialize the zed camera
        if self.zed.open(init_params) != sl.ERROR_CODE.SUCCESS:
            raise CameraActivationError

        # create a matrix to store raw zed output
        self.image_width = self.zed.get_camera_information().camera_resolution.width
        self.image_height = self.zed.get_camera_information().camera_resolution.height
        self.raw_zed_data = sl.Mat(self.image_width,
                                   self.image_height,
                                   mat_type=sl.MAT_TYPE.U8_C4,
                                   memory_type=sl.MEM.CPU)

        # set up matrix to store depth map if specified
        if self.include_depth:
            self.raw_depth_data = sl.Mat(self.image_width,
                                   self.image_height,
                                   mat_type=sl.MAT_TYPE.U8_C4,
                                   memory_type=sl.MEM.CPU)

        # start camera sensor
        self.started = True
        print('[info] camera sensor: ON')

        # retrieve and update image frame data
        self.zed.retrieve_image(self.raw_zed_data,  sl.VIEW.LEFT)
        self.image_frame = self.raw_zed_data.get_data()
        if self.include_depth:
            self.zed.retrieve_image(self.raw_depth_data, sl.VIEW.DEPTH)
            self.depth_map = self.raw_depth_data.get_data()

        # start update thread to continuously collect image data
        self.thread = Thread(target=self._update, args=())
        self.thread.start()

    def _update(self) -> None:
        """
        This function is called by the start() function
        It continosly grabs and updates image and depth map information
        Information is updated to the class variables self.image_frame and self.depth_map
        Additionally the image/depth map data is sent to a buffer for later access
        :return: None
        """

        # update frames continuously while the sensor is ON
        while self.started:

            if self.zed.grab(sl.RuntimeParameters()) == sl.ERROR_CODE.SUCCESS:

                # get raw camera image data
                self.zed.retrieve_image(self.raw_zed_data, self.camera_view)
                image_ocv = self.raw_zed_data.get_data()[:, :, 0:3]

                # if specified get raw camera depth data
                if self.include_depth:
                    self.zed.retrieve_image(self.raw_depth_data, sl.VIEW.DEPTH)
                    #self.zed.retrieve_measure(self.raw_depth_data, sl.MEASURE.DEPTH) # uncomment to measure
                    depth_ocv = self.raw_depth_data.get_data()[:, :, 0:3]

                # update
                self.read_lock.acquire()
                self.image_frame = image_ocv

                if self.include_depth:
                    self.depth_map = depth_ocv

                self.read_lock.release()

                # send to buffer
                ZedCameraSensor.send_to_buffer('zed_image', image_ocv)
                ZedCameraSensor.send_to_buffer('zed_depth_map', depth_ocv)
                #print('[info]: image saved to buffer')

                ''' WHAT IS THE POINT OF THESE 3 LINES BELOW?? '''
                timestamp = self.zed.get_timestamp(sl.TIME_REFERENCE.IMAGE).get_seconds()
                dt_object = datetime.fromtimestamp(timestamp)
                time.sleep(0.06)

            else:
                raise ImageCaptureError('camera failed to return an image')

    def get_image_frame(self) -> np.array:
        """
        Grabs and returns the current image frame
        :return: returns a np.array of shape [image_height, image_width, num_channels]
        """
        '''WHY IS READ LOCK NOT USED IN STAN'S CODE??'''
        '''BUT ITS USED IN THE LIDAR CLASS???'''
        self.read_lock.acquire()
        image_frame = self.image_frame.copy()
        self.read_lock.release()
        return image_frame

    def get_depth_map(self) -> np.array:
        """
        Grabs annd returns the current depth map
        :return: returns a np.array of shape [image_height, image_width]
        """
        '''WHY IS READ LOCK NOT USED IN STAN'S CODE??'''
        '''BUT ITS USED IN THE LIDAR CLASS???'''
        self.read_lock.acquire()
        depth_image = self.depth_map.copy()
        self.read_lock.release()
        return depth_image

    def exit(self) -> None:
        """
        Shutdown Camera Sensor
        :return: None
        """
        self.started = False
        self.thread.join()  # WHY IS THIS USED?
        self.zed.close()
        print('\n[info] camera sensor: OFF')

    @staticmethod
    def mmap_write(filename: str,
                   data: np.array) -> None:
        """
        *** will need to create a parameter to help switch between image and depth map
        ### because the data types might be different between the depth and image
        This function is uses a numpy memory map to store the image/depth map data
        :param filename: [string] the name of the file associated with the data
        :param data: an numpy.array of the image or depth map data
        :return: None
        """
        # set up memmap
        if os.path.exists(filename):
            zed_img_path = np.memmap(filename, dtype='uint8', mode='r+', shape=data.shape)
        else:
            zed_img_path = np.memmap(filename, dtype='uint8', mode='w+', shape=data.shape)

        # insert data
        zed_img_path[:] = data[:]

    @staticmethod
    def mmap_read(filename: str, image_shape: tuple) -> np.array:
        """
        This function is used to read the image/depth map information from the memorymapped location
        :param filename: [string] the name of the file associated with the data
        :param image_shape: [tuple] shape of the image data to be retrieved
        :return:
        """
        if os.path.exists(filename):
            # grab and return the memory mapped data[ie: np.array]
            zed_img_path = np.memmap(filename, dtype='uint8', mode='c', shape=image_shape)
            return zed_img_path
        else:
            raise FileNotFoundError

    @staticmethod
    def send_to_buffer(filename: str, image_arr: np.array) -> None:
        """
        :param filename: [string] the name of the file associated with the data
        :param image_arr: an numpy.array of the image or depth map data
        :return:
        """
        start = time.perf_counter()
        ZedCameraSensor.mmap_write(filename, image_arr)
        #print(f'[info] Total write time: {time.perf_counter() - start}')

    @staticmethod
    def get_from_buffer(filename: str, image_shape: tuple) -> tuple:
        """
        :return:
        """
        start = time.perf_counter()

        # read 3 point clouds
        image = ZedCameraSensor.mmap_read(filename, image_shape)
        #print(f'Total time = {time.perf_counter() - start}')

        return image


# if __name__ == "__main__":
#     camera1 = ZedCameraSensor()
#     camera1.start()
#     time.sleep(0.1)
#
#     w = camera1.image_width
#     h = camera1.image_height
#     c = camera1.num_channels
#
#     while True:
#         frame = camera1.get_from_buffer('zed_depth_map', (h, w, c))
#         frame2 = camera1.get_from_buffer('zed_image', (h, w, c))
#
#         #cv2.imshow("image", frame)
#         cv2.imshow("depth", frame)
#
#         key = cv2.waitKey(30)
#
#         if key == ord('q'):
#             break
#
#     camera1.exit()





































