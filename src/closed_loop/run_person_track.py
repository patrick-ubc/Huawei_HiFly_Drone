"""
Closed-Loop Detection + Tracking System
Control relies on feedback from in a closed-loop manner - enable drone to automatically adjust itself without user intervention to detect and track an
object of interest

+ Visual Servoing with Robotics
+ Implementation of PID of object detection

:class:
    PIDController  - using proportional integral derivative to enable continuous modulated control
        1. modify to return the coordinates of detected hand - assuming only 1 object-of-interest, we only grab the result from the bbox with highest confidence

"""
import time
import sys
import os
import cv2
import numpy as np
from importlib import import_module
from abc import abstractmethod
from djitellopy import Tello
import pickle
import argparse

sys.path.append("..")
sys.path.append("../lib")

from utils.uav_utils import connect_uav 
from utils.params import params
from atlas_utils.presenteragent import presenter_channel
from atlas_utils.acl_image import AclImage
from DecisionFilter import DecisionFilter
"""
:class: TelloPIDController - Base class 
    Has Compensator, Setpoint, Actuator and Process Variable. Initializes a Tello UAV object
    :input:
        + PID
    
    :components:
        + bbox_compensator - internal method for calculating distance b/w detected bbox of ToI and central point
        + Set-point        - pre-set attribute based on Tello data stream dimensions
        + Actuator         - internal method to stabilize droen and track ToI based on bbox_compensator's output
                        I.e.: compensator says bbox is far to the right, actuator rotate camera to the right to adjust

        + Accepts a connected TelloUAV - takeoff and streamon;
            - once stream is stablized run OD, 

:class: TelloPIDController - children class
    + Model specific - override method to extract inference output
    + add :param: uav back to TelloPIDController 

"""
class TelloPIDController:
    detectors = params["task"]["object_detection"]

    def __init__(self, pid):
        self.pid = pid
        self.setpoint = None
        self.history = []

    @staticmethod
    def _load_mp(detector_name):
        """Internal method for children class to load specified MP
        :param:
            + detector_name - Key name of detection model
        Returns
            A fully initialized ModelProcessor object
        """
        model_info = TelloPIDController.detectors[detector_name]
        processor = model_info["model_processor"]
        MP = import_module(f"model_processors.{processor}")
        MP = getattr(MP, "ModelProcessor")
        return MP(model_info)
    
    @staticmethod
    def _load_filter(Filter, **kwargs):
        """Internal method to load Inference Filter
        :param:
            filter_name - a Filter Object (i.e. DecisionFilter)
        Returns
            an initialized Filter object
        """
        inference_filter = Filter(**kwargs)
        return inference_filter

    def init_uav(self):
        """
        Initiate closed-loop tracking sequence, drone takeoff and parallelize streaming and control
        Returns
            None
        """
        try:
            self.uav = Tello()
            self.uav.connect()
            print("UAV connected successfully!")
            print(f"Current battery percentage: {self.uav.get_battery()}")
            self.uav.streamoff()
            self.uav.streamon()
            self.uav.left_right_velocity = 0
            self.uav.forward_backward_velocity = 0
            self.uav.up_down_velocity = 30
            self.uav.yaw_velocity = 0    
            return True
        except Exception as e:
            raise Exception("Failed to connect to Tello UAV, please try to reconnect")

    def fetch_frame(self):
        frame = self.uav.get_frame_read().frame
        if self.setpoint is None:
            cx, cy = frame.shape[1] // 2, frame.shape[0] // 2
            self.setpoint = (cx, cy)
        return frame

    def _get_feedback(self, frame):
        """Obtains feedback (inference result) from model. Preprocess and execute the model using ModelProcessor.  
        :param:
            + frame - input frame for inference
        Returns
            Model's inference output (i.e: a list containing inference information such as bbox, num_detections, etc.)
        """
        if self.setpoint is None:
            cx, cy = frame.shape[1] // 2, frame.shape[0] // 2
            self.setpoint = (cx, cy)
        preprocessed = self.model_processor.preprocess(frame)
        infer_output = self.model_processor.model.execute([preprocessed, self.model_processor._image_info])
        return infer_output
    
    def _pid(self, error, prev_error):
        """PID Output signal equation"""
        return self.pid[0]*error + self.pid[1]*(error-prev_error) + self.pid[2]*(error-prev_error)

    @abstractmethod
    def _unpack_feedback(self, inference_info, frame):
        pass

    @abstractmethod
    def _track(self, inference_info):
        pass

    @abstractmethod
    def _search(self):
        pass


class ObjectTracker(TelloPIDController):
    def __init__(self, pid):
        super().__init__(pid)
        self.model_processor = self._load_mp("yolov3")
        self.inference_filter = self._load_filter(DecisionFilter, fps=5)
        self.search_mode = True
        self.track_mode = False
           
    def _unpack_feedback(self, infer_output, frame, toi="person"):
        """ Extract Process Variables from model's inference output info of input frame. The largest bbox of the same ToI label will be marked as ToI
        :params:
            infer_output - model's inference result from executing model on a frame
            frame        - input frame for inference
            toi          - Target-of-Interest 
        Returns
            process_var_center  - Process Variable - ToI's bbox center
            process_var_bbox    - Process Variable - ToI's bbox area
            result_img   - inference result superimposed on original frame
        """
        box_num = infer_output[1][0, 0]
        box_info = infer_output[0].flatten()
        scale = max(frame.shape[1] / self.model_processor._model_width, frame.shape[0] / self.model_processor._model_height)
        
        # Iterate the detected boxes and look for label that matches ToI with the largest bbox area
        process_var_bbox_area = 0
        process_var_bbox_center = None

        for n in range(int(box_num)):
            ids = int(box_info[5 * int(box_num) + n])
            label = self.model_processor.labels[ids]

            if label == toi:
                score = box_info[4 * int(box_num)+n]
                top_left_x = int(box_info[0 * int(box_num)+n] * scale)
                top_left_y = int(box_info[1 * int(box_num)+n] * scale)
                bottom_right_x = int(box_info[2 * int(box_num) + n] * scale)
                bottom_right_y = int(box_info[3 * int(box_num) + n] * scale)
                cx = (top_left_x + bottom_right_x) // 2
                cy = (top_left_y + bottom_right_y) // 2
                center = (cx, cy)
                area = (bottom_right_x - top_left_x) * (bottom_right_y - top_left_y)

                if area > process_var_bbox_area:
                    process_var_bbox_area = area
                    process_var_bbox_center = center

                    cv2.rectangle(frame, (top_left_x, top_left_y), (bottom_right_x, bottom_right_y), (0,0,255), 2)
                    cv2.circle(frame, center, 1, (0,0,255), -1)
        
        return frame, (process_var_bbox_area, process_var_bbox_center)

    def _pid_controller(self, process_vars, prev_x_err, prev_y_err):
        """Closed-Loop PID Object Tracker (Compensator + Actuator)
        Calculates the Error value from Process variables and compute the require adjustment for the drone. 
        XY error is error between setpoint (frame center) and process_var_bbox_center
        Process Variable Area - for calculating the distance between drone and ToI (if it is over 80% of frame, then drone needs to move back)
                    Info obtained from inference bbox
                    + forward and backward motion of drone
        Process Variable center - for calculating how much to adjust the camera angle and altitude of drone
                    x_err: angle rotation
                    y_err: elevation to eye-level
        :params:
            + process_vars - Tuple(process variables bbox area and process variable bbox center)
            + prev_x_err   - x error from previous control loop
            + prev_y_err   - y error from previous control loop
        Returns
            x_err, y_err   - current control loop error 
        """
        area = process_vars[0]
        center = process_vars[1]
        print(f"Area: {area}")
        print(f"Center: {center}")

        if area == 0 and center is None:
            return prev_x_err, prev_y_err
        
        x_err = process_vars[1][0] - self.setpoint[0]       # rotational err
        y_err = process_vars[1][1] - self.setpoint[1]       # elevation err

        print(f"\nXY error: {x_err}, {y_err}")
        print(f"BBox Area: {area}")

        self.setpoint_area = [150000, 200000]  # lower and upper bound for Forward&Backward Range-of-Motion    

        # Velocity signals for the drone
        forward_backward_velocity = 0
        left_right_velocity = 0
        up_down_velocity = 0
        yaw_velocity = 0

        """Compensator: calcuate the amount adjustment needed"""
        # Localization of ToI to the center - adjusts angle
        if x_err != 0:
            # yaw_velocity = pid[0]*x_err + pid[1]*(x_err - prev_x_err)
            yaw_velocity = self._pid(x_err, prev_x_err)
            yaw_velocity = int(np.clip(yaw_velocity, -100, 100))
            print(f"YAW Velocity: {yaw_velocity}")

        # Localization of ToI to be at eye level - adjust altitude 
        # if y_err != 0:
        #     up_down_velocity = self._pid(y_err, prev_y_err)
        #     up_down_velocity = int(np.clip(up_down_velocity, -100, 100))

        # Stablization: forward and backward motion
        if area > self.setpoint_area[0] and area < self.setpoint_area[1]:
            forward_backward_velocity = 0
        elif area < self.setpoint_area[0]:
            forward_backward_velocity = 20
        elif area > self.setpoint_area[1]:
            forward_backward_velocity = -20

        history = {
            "left_right_velocity": left_right_velocity,
            "forward_backward_velocity": forward_backward_velocity, 
            "up_down_velocity": up_down_velocity,
            "yaw_velocity": yaw_velocity,
            "x_err": x_err,
            "y_err": y_err,
            "pv_bbox_area": area,
            "pv_center": center
        }
        self.history.append(history)

        """Actuator - adjust drone's motion to converge to setpoint"""
        self.uav.send_rc_control(left_right_velocity, forward_backward_velocity, up_down_velocity, yaw_velocity)
        return x_err, y_err

    # """Uncomment to revert back to simplicit tracking"""
    # # def track(self, frame, prev_x_err, prev_y_err, toi="person"):
    # #     infer_output = self._get_feedback(frame)
    # #     result_img, process_vars = self._unpack_feedback(infer_output, frame, toi)
    # #     x_err, y_err = self._pid_controller(process_vars, prev_x_err, prev_y_err)
    # #     return result_img, x_err, y_err

    def _track(self, process_vars, prev_x_err, prev_y_err):
        x_err, y_err = self._pid_controller(process_vars, prev_x_err, prev_y_err)
        return x_err, y_err

    def _search(self,):
        """Send RC Controls to drone to try to find ToI"""
        self.uav.send_rc_control(0,0,0,40)
        return

    def _manage_state(self, frame, toi="person"):
        """State Manager
        Infer surroundings to check if ToI is present, pass feedback to Filter to smooth out detection result. Break out of Search Mode 
        and enter Track Mode if ToI is consistently present. Vice versa.
        :params:
            + frame     - input frame from video stream
            + toi       - Target-of-Interest, defaults to Person for Person detection
        Returns
            result_img   - inference result superimposed on frame
            process_vars - Tuple() of process variables
        """
        infer_output = self._get_feedback(frame)
        result_img, process_vars = self._unpack_feedback(infer_output, frame, toi)

        area, center = process_vars[0], process_vars[1]

        sample_val = center if center is None else "Presence"
        mode_inference = self.inference_filter.sample(sample_val)
        if mode_inference == "MDOE_INFERENCE_SAMPLING":
            pass
        elif mode_inference == "Presence": 
            self.track_mode = True
            self.search_mode = False
        elif mode_inference is None:
            self.track_mode = False
            self.search_mode = True
        
        print(f"mode_inference = {mode_inference}")
        print(f"Search Mode: {self.search_mode}; Track Mode: {self.track_mode}")

        return result_img, process_vars
    
    def run_state_machine(self, frame, prev_x_err, prev_y_err):
        result_img, process_vars = self._manage_state(frame)
        if self.search_mode:
            self._search()
            return prev_x_err, prev_y_err
        if self.track_mode:
            x_err, y_err = self._track(process_vars, prev_x_err, prev_y_err)
            return x_err, y_err 
    
def get_latest_run(dir_name):
    """Utility function to get the most updated run for experimental analysis"""
    dir_list = os.listdir(dir_name) 
    fname = [int(filename.split(".")[0].split("_")[-1]) for filename in dir_list]
    fname.sort(key=lambda x: int(x))
    return fname[-1]

    
if __name__ == "__main__":
    latest_run = get_latest_run("test_run") + 1

    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", nargs="+", help="PID List", required=True)
    parser.add_argument("--rn", help="Test run name", default=latest_run)
    args = parser.parse_args()

    ## Fixed Parameters ##
    SRC_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PRESENTER_SERVER_CONF = os.path.join(SRC_PATH, "uav_presenter_server.conf")
    x_err, y_err = 0, 0
    in_flight = False
    test_run_end = False
    timeout = time.time() + 60


    pid = [float(val) for val in args.pid]
    latest_run = str(args.rn)

    obj_tracker = ObjectTracker(pid)
    uav_inited = obj_tracker.init_uav()
    if not uav_inited: 
        raise Exception("Tello not inited")
    
    # chan = presenter_channel.open_channel(PRESENTER_SERVER_CONF)
    # if chan is None:
        # raise Exception("Open presenter channel failed")

    while not test_run_end:
        if not in_flight:
            in_flight = True
            obj_tracker.uav.takeoff()
        
        frame_org = obj_tracker.fetch_frame()
        if frame_org is None: raise Exception("frame is none")

        x_err, y_err = obj_tracker.run_state_machine(frame_org, x_err, y_err)

        if time.time() > timeout:        
            test_run_end = True

    obj_tracker.uav.land()
    obj_tracker.uav.streamoff()

    test_run = str(latest_run)
    test_file = f"test_run/test_run_{test_run}.pkl"
    with open(test_file, "wb") as test_run_data:
        pickle.dump(obj_tracker.history, test_run_data)

    print(f"Test Run {test_run}: End")

    sys.exit()