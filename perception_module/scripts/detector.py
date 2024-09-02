#!/home/sp/miniconda3/envs/percept/bin/python

# from utils.group import group as gemini_group
from utils.gpt4 import group as gemini_group   # gpt4o
# from utils.gemini import group as gemini_group
from visualization_msgs.msg import Marker, MarkerArray
import tf.transformations as tf_trans
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import PoseArray,PoseStamped,Pose
from detection_msgs.msg import BoundingBoxes,BoundingBox,mapping,Group,Groups
from cv_bridge import CvBridge, CvBridgeError
from collections import deque
from datetime import datetime
import message_filters

import threading
import time
import rospy
import tf2_ros
import cv2
import os
import numpy as np
import tf
import json
from detection_msgs.msg import mapping
from std_msgs.msg import String,Bool

import rospkg
rospack = rospkg.RosPack()
package_path = rospack.get_path('perception_module')


class DataQueue:
    def __init__(self,maxlen) -> None:
        self.datas = deque(maxlen=maxlen)
        self.times = deque(maxlen=maxlen) 

class Mapping_frame:
    def __init__ (self,msg:mapping):
        self.stamp = msg.header.stamp
        self.id_list = []
        self.points = []
        self.pose_array = []
        self.vel_x_list = []
        self.vel_y_list = []

        self.points = [[msg.point_xs[i],msg.point_ys[i]] for i in range(len(msg.id_list))]
        self.pose_array = [msg.pose_list.poses[i] for i in range(len(msg.id_list))]
        self.id_list = [msg.id_list[i] for i in range(len(msg.id_list))]
        self.vel_x_list = [msg.vel_x_list[i] for i in range(len(msg.id_list))]
        self.vel_y_list = [msg.vel_y_list[i] for i in range(len(msg.id_list))]

    def add(self,frame:"Mapping_frame"):
        self.id_list.extend(frame.id_list)
        self.points.extend(frame.points)
        self.pose_array.extend(frame.pose_array)
        self.vel_x_list.extend(frame.vel_x_list)
        self.vel_y_list.extend(frame.vel_y_list)


class Detector_Config:
        # 保留detector的配置信息以及一些全局变量
    def __init__(self, config_path):
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.folder_path = package_path+ self.config["folder_path"] + datetime.now().strftime("%Y-%m-%d_%H%M%S")

        self.frames = deque(maxlen=self.config["frame_lenth"])

        self.bounding_boxes_frames = DataQueue(self.config["keep_time"])
        self.image_frames = DataQueue(self.config["keep_time"])
        self.mapping_frames = DataQueue(self.config["keep_time"])

        self.id_already_met = []
        self.frame = []
        self.cur_frame = []
        self.keyframe = []
        self.group_list = []
        self.keyframe_count = 0

class Detector_SubModule:
    def __init__(self) -> None:
        self.bridge = CvBridge()
        self.listener = tf.TransformListener()
        self.rate = rospy.Rate(10)

class Detector:
    def __init__(self) -> None:

        # 初始化配置
        config_path = package_path + "config/detector_config.json"
        self.config_module = Detector_Config(config_path)

        # 其他初始化代码
        self.submodule = Detector_SubModule()

        # 初始化订阅者和发布者
        self._initialize_Sub_Pub()

        # 线程初始化
        self._initialize_threads()

        # 创建文件夹
        os.mkdir(self.config_module.folder_path)

        rospy.loginfo("Detector node start")

    def _initialize_Sub_Pub(self):
        # the only publisher we need
        self.group_pub = rospy.Publisher("group", Groups, queue_size=1)

        self.gpt_flag_sub = rospy.Subscriber("/middle_flag",Bool,self.gpt_flag_cb)
        self.stop_flag_pub = rospy.Publisher("/stop_flag",Bool,queue_size=1)
        self.left_map_sub = message_filters.Subscriber("/mapping/left", mapping)
        self.right_map_sub = message_filters.Subscriber("/mapping/right", mapping)
        ts = message_filters.TimeSynchronizer([self.left_map_sub,self.right_map_sub], 10)
        ts.registerCallback(self._mapping_cb)

        self.image_sub = message_filters.Subscriber(self.config_module.config["image_topic"], Image)
        self.bounding_boxes_sub = message_filters.Subscriber(self.config_module.config["BoundingBoxes_topic"], BoundingBoxes)
        ts2 = message_filters.TimeSynchronizer([self.image_sub,self.bounding_boxes_sub], 10)
        ts2.registerCallback(self._bounding_boxes_cb)
        self.group_sub = rospy.Subscriber("/test_group",String,self.group_test_cb)

    def _initialize_threads(self):
        pass
        # 初始化处理线程

        thread_get_group = threading.Thread(target=self._get_group)
        thread_get_group.daemon = True
        thread_get_group.start()

    def gpt_flag_cb(self,msg:Bool):
        if msg.data == False:
            try:
                # 获取keyframe里面的所有id
                id_in_keyframe = [id for [id,_,_,_] in self.config_module.cur_frame[1]]

                if id_in_keyframe == [] or len(id_in_keyframe) == 1:
                    # rospy.loginfo("No keyframe")
                    # 不会发布stop_flag
                    return
                else: 
                    # keyframe里面的id不在id_already_met里面
                    for id in id_in_keyframe:
                        if id not in self.config_module.id_already_met:
                            rospy.loginfo("id not in id_already_met")
                            rospy.loginfo("id: %d",id)
                            rospy.loginfo("id_already_met: %s",self.config_module.id_already_met)
                            # 添加整个id_in_keyframe到id_already_met
                            self.config_module.id_already_met.extend(id_in_keyframe)
                            # 停下来
                            stop_flag = Bool()
                            stop_flag.data = True
                            self.stop_flag_pub.publish(stop_flag)

                            rospy.loginfo("Save keyframe and publish group")
                            keyframe = self.config_module.keyframe.copy()
                            self._pub_group(keyframe)
                            return
                    # keyframe里面的id都在id_already_met里面
                    # rospy.loginfo("id in id_already_met")
                    return
            except Exception as e:
                rospy.logwarn(e)
    # def gpt_flag_cb(self,msg:Bool):
    #     rospy.loginfo("Get gpt_flag: %s",msg.data)
    #     if msg.data == False:
    #         try:
    #             # 获取keyframe里面的所有id
    #             id_in_keyframe = [id for [id,_,_,_] in self.config_module.cur_frame[1]]

    #             if id_in_keyframe == [] or len(id_in_keyframe) == 1:
    #                 # rospy.loginfo("No keyframe")
    #                 # 不会发布stop_flag
    #                 return
    #             else: 
    #                 # 停下来
    #                 stop_flag = Bool()
    #                 stop_flag.data = True
    #                 self.stop_flag_pub.publish(stop_flag)

    #                 rospy.loginfo("Save keyframe and publish group")
    #                 keyframe = self.config_module.keyframe.copy()
    #                 self._pub_group(keyframe)
    #                 return
    #         except Exception as e:
    #             rospy.logwarn(e)

    def _get_group(self):
        while not rospy.is_shutdown():
            try:
                self.run()
            except Exception as e:
                rospy.logwarn(e)

    def _pub_group(self,keyframe:list):
        # cur_time = None
        try:
            image_path = self.config_module.folder_path+str(self.config_module.keyframe_count) +".jpg"
            cv2.imwrite(image_path,keyframe[0])
            # self.config_module.group_list = gemini_group(image_path)
            ## 修改为直到group_list不为空为止
            # 第一次获取group_list
            
            while self.config_module.group_list == []:
                self.config_module.group_list = gemini_group(image_path)
                rospy.loginfo("Get group_list")
            # id_list = [id for [id,_,_,_] in keyframe[1]]
            # self.config_module.group_list =[id_list] # [[id,pose,vel_x,vel_y]]

            # delay 2s
            # rospy.loginfo("Delay 2s")
            # time.sleep(2)
            rospy.loginfo("Publish group")
            groups_msg = Groups()
            groups_msg.header.stamp = keyframe[2]
            groups_msg.header.stamp = rospy.Time.now()
            groups_msg.header.frame_id = "map"

            for group in self.config_module.group_list:
                group_msg  = Group()
                count = 0
                for [id,pose,vel_x,vel_y] in keyframe[1]:
                    if id in group_msg.group_id_list:
                        rospy.loginfo("id in group")
                        continue
                    if id in group:
                        p = Pose()
                        p.position.x = pose.position.x
                        p.position.y = pose.position.y
                        group_msg.group_pose_list.poses.append(p)
                        group_msg.group_id_list.append(id)
                        group_msg.group_vel_x_list.append(vel_x)
                        group_msg.group_vel_y_list.append(vel_y)
                        count += 1
                if count < 2:
                    continue
                else:
                    groups_msg.group_list.append(group_msg)
            # rospy.loginfo(groups_msg)
            rospy.loginfo(groups_msg)
            if len(groups_msg.group_list) == 0:
                rospy.loginfo("No group")
                stop_flag = Bool()
                stop_flag.data = False
                self.stop_flag_pub.publish(stop_flag)
                return
            self.group_pub.publish(groups_msg)

            stop_flag = Bool()
            stop_flag.data = False
            self.stop_flag_pub.publish(stop_flag)
            
            # 变换重置
            self.config_module.group_list = []

            rospy.loginfo("Delay 2s")
            time.sleep(2)
        except Exception as e:
            rospy.logwarn(e)

    def group_test_cb(self,msg:String):
        group = msg.data.split()
        group_list = [[int(i) for i in group]]
        rospy.loginfo("Get group test")
        rospy.loginfo(group_list)
        rospy.loginfo("id in frame")
        for [id,pose,vel_x,vel_y] in self.config_module.keyframe[1]:
            rospy.loginfo(id)
        keyframe = self.config_module.keyframe.copy()
        try:
            rospy.loginfo("Publish group")
            groups_msg = Groups()
            groups_msg.header.stamp = keyframe[2]
            groups_msg.header.stamp = rospy.Time.now()
            groups_msg.header.frame_id = "map"

            # 遍历所有group
            for group in group_list:
                group_msg  = Group()
                count = 0
                # 遍历keyframe中的所有id
                for [id,pose,vel_x,vel_y] in keyframe[1]:
                    if id in group_msg.group_id_list:
                        rospy.loginfo("id in group")
                        continue
                    if id in group:
                        p = Pose()
                        p.position.x = pose.position.x
                        p.position.y = pose.position.y
                        group_msg.group_pose_list.poses.append(p)
                        group_msg.group_id_list.append(id)
                        group_msg.group_vel_x_list.append(vel_x)
                        group_msg.group_vel_y_list.append(vel_y)
                        count += 1
                if count < 2:
                    continue
                else:
                    groups_msg.group_list.append(group_msg)
            rospy.loginfo(groups_msg)
            self.group_pub.publish(groups_msg)
        except Exception as e:
            rospy.logwarn(e)

    def _mapping_cb(self,left_map_msg:mapping,right_map_msg:mapping):
        left_map = Mapping_frame(left_map_msg)
        right_map = Mapping_frame(right_map_msg)
        left_map.add(right_map)
        # rospy.loginfo(left_map.id_list)
        
        self.config_module.mapping_frames.datas.append(left_map)
        self.config_module.mapping_frames.times.append(left_map_msg.header.stamp)

    def _bounding_boxes_cb(self,image:Image,msg:BoundingBoxes):
        self.config_module.bounding_boxes_frames.datas.append(msg.bounding_boxes)
        self.config_module.bounding_boxes_frames.times.append(msg.header.stamp)
        try:
            cv_image = self.submodule.bridge.imgmsg_to_cv2(image, "bgr8")
            # cv2.imshow("image",cv_image)
            # cv2.waitKey(10)
            self.config_module.image_frames.datas.append(cv_image)
            self.config_module.image_frames.times.append(msg.header.stamp)
        except CvBridgeError as e:
            rospy.logwarn(e)

    def _set_frame(self):
        if len(self.config_module.bounding_boxes_frames.datas) == 0 or len(self.config_module.mapping_frames.datas)== 0:
            # rospy.loginfo("No frame")
            return  False
        else:
            boundingboxes_frame = self.config_module.bounding_boxes_frames.datas[-1]
            image_frame = self.config_module.image_frames.datas[-1]
            boundingboxes_time = self.config_module.bounding_boxes_frames.times[-1]
            mapping_frame = None
            mapping_frame_time = None
            for i in range(len(self.config_module.mapping_frames.datas)):
                if mapping_frame == None or abs(self.config_module.mapping_frames.times[i] - boundingboxes_time) < abs(mapping_frame_time - boundingboxes_time):
                    mapping_frame = self.config_module.mapping_frames.datas[i]
                    mapping_frame_time = self.config_module.mapping_frames.times[i]
            # mapping_frame 包括id_list,points,pose_array
            self.config_module.frame = [image_frame,boundingboxes_frame,mapping_frame,boundingboxes_time]
            self.config_module.cur_frame = self.auto_remove(self.config_module.frame)
            self.config_module.frames.append(self.config_module.cur_frame)
            return True
    
    @staticmethod
    def auto_remove(frame): # list[Image,BoundingBoxes,Mapping_frame]
        image = frame[0]
        boundingboxes = frame[1]
        mapping_frame = frame[2] # Mapping_frame
        id_list = []
        pose_list = []
        vel_x_list = []
        vel_y_list = []

        flag = 0
        for boundingbox in boundingboxes:
            if boundingbox.ymax-boundingbox.ymin < 130:
                continue
            else:
                flag = 1
        if flag == 0:
            return [image,[],frame[3]]  
        for boundingbox in boundingboxes:
            boundingbox:BoundingBox
            if boundingbox.Class != "person":
                continue
            boundingbox_index = -1
            for i in range(len(mapping_frame.id_list)):
                thre = 0.2
                x_thre = (boundingbox.xmax - boundingbox.xmin) * thre
                if boundingbox.xmin <= (mapping_frame.points[i][0] - x_thre) and (mapping_frame.points[i][0]<= boundingbox.xmax+x_thre) and boundingbox.ymin <= mapping_frame.points[i][1] and mapping_frame.points[i][1] <= boundingbox.ymax:             
                    # cv2.circle(image,(int(mapping_frame.points[i][0]),int(mappingframe.points[i][1])),3,(0,255,0),-1)
                    # cv2.putText(image,str(mapping_frame.id_list[i]),(int(mapping_frame.points[i][0]),int(mapping_frame.points[i][1])),cv2.FONT_HERSHEY_SIMPLEX,1,(0,255,0),2)

                    if boundingbox_index == -1 or mapping_frame.points[i][1] <= mapping_frame.points[boundingbox_index][1]:
                        boundingbox_index = i

            # if boundingbox_index == 0:
            #     box_size = 10
            #     center_x = int((boundingbox.xmin + boundingbox.xmax) / 2)
            #     center_y = int((boundingbox.ymin + boundingbox.ymax) / 2)
            #     cv2.rectangle(image, (center_x - box_size, center_y - 2*box_size), (center_x + 3* box_size, center_y + box_size), (0, 0, 0), -1)
            #     cv2.putText(image, "no", ( center_x-5, center_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255),1)
            #     continue

            boundingbox_id = mapping_frame.id_list[boundingbox_index]
            id_list.append(boundingbox_id)
            pose_list.append(mapping_frame.pose_array[boundingbox_index])
            vel_x_list.append(mapping_frame.vel_x_list[boundingbox_index])
            vel_y_list.append(mapping_frame.vel_y_list[boundingbox_index])
            # cv2.putText(image,str(boundingbox_id),(int((boundingbox.xmin+boundingbox.xmax)/2),int((boundingbox.ymin+boundingbox.ymax)/2)),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2)
            # 设置boundingbox 中心部分为黑底
            # center_x = int((boundingbox.xmin + boundingbox.xmax) / 2)
            # center_y = int((boundingbox.ymin + boundingbox.ymax) / 2)
            # box_size = 10
            # cv2.rectangle(image, (center_x - box_size, center_y - 2*box_size), (center_x + 2* box_size, center_y + box_size), (0, 0, 0), -1)
            # # 设置为白字，同时起始点向左边移动3个像素以居中显示
            # cv2.putText(image, str(boundingbox_id), ( center_x-5, center_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255),1)
            # 设置在右上角显示id
            cv2.rectangle(image, (boundingbox.xmax, boundingbox.ymin), (boundingbox.xmax+30, boundingbox.ymin+20), (0, 0, 0), -1)
            cv2.putText(image, str(boundingbox_id), (boundingbox.xmax+5, boundingbox.ymin+15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255),1)
            
        for i in range (len(mapping_frame.id_list)):
            if i not in id_list:
                pass
                # cv2.putText(image,str(mapping_frame.id_list[i]),(int(mapping_frame.points[i][0]),int(mapping_frame.points[i][1])),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,0),2)


        return [image,[[id_list[i],pose_list[i],vel_x_list[i],vel_y_list[i]] for i in range(len(id_list))],frame[3]]

    def _set_keyframe(self):
        if len(self.config_module.frames) == 0:
            return False
        else:
            if self.config_module.keyframe == []: # 若keyframe为空，则直接设置为当前帧
                # rospy.loginfo("Set first keyframe")
                self.config_module.keyframe = self.config_module.frames[-1]
                self.config_module.keyframe_count += 1
                return True
            else:
                if len(self.config_module.keyframe[1]) <= len(self.config_module.cur_frame[1]):
                    # rospy.loginfo("Set keyframe with more person")
                    self.config_module.keyframe = self.config_module.frames[-1]
                    self.config_module.keyframe_count += 1
                    return True
                if self.config_module.cur_frame[2] - self.config_module.keyframe[2] > rospy.Duration(secs=5):
                    rospy.loginfo("Set keyframe with time")
                    self.config_module.keyframe = self.config_module.frames[-1]
                    self.config_module.keyframe_count += 1
                    return True
            return True
        
    def run(self):
        if self._set_frame():
            self._set_keyframe()
            cv2.imshow("image",self.config_module.cur_frame[0])
            # cv2.imshow("keyframe",self.config_module.keyframe[0])
            # cv2.waitKey(10)
            if cv2.waitKey(10) & 0xFF == ord('s'):
                    rospy.loginfo("Save keyframe")
                    keyframe = self.config_module.keyframe.copy()
                    self._pub_group(keyframe)
            return
            # todo add cvimshow for frame and keyframe
        else:
            return 
        
if __name__ == '__main__':
    rospy.init_node('detector', anonymous=True)
    detector = Detector()
    rate = rospy.Rate(10)
    while not rospy.is_shutdown():
        # detector.run()
        rate.sleep()
    rospy.spin()