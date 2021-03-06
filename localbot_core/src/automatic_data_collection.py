#!/usr/bin/env python3

# stdlib

import sys
import argparse
import copy
import random
import math

# 3rd-party
import rospy
import tf
import numpy as np
import trimesh
from geometry_msgs.msg import Point, Pose, Quaternion
from interactive_markers.interactive_marker_server import *
from interactive_markers.menu_handler import *
from visualization_msgs.msg import *
from gazebo_msgs.srv import SetModelState, GetModelState, GetModelStateRequest, SetModelStateRequest
from colorama import Fore
from scipy.spatial.transform import Rotation as R

from localbot_core.src.save_dataset import SaveDataset
from localbot_core.src.utilities import *
from localbot_core.src.utilities_ros import *


class AutomaticDataCollection():

    def __init__(self, model_name, seq, dbf=None, uvl=None, model3d_config=None, fast=None, save_dataset=True):
        self.set_state_service = rospy.ServiceProxy(
            '/gazebo/set_model_state', SetModelState)
        self.menu_handler = MenuHandler()
        self.model_name = model_name  # model_name = 'localbot'

        self.dbf = dbf

        rospy.wait_for_service('/gazebo/get_model_state')
        self.get_model_state_service = rospy.ServiceProxy(
            '/gazebo/get_model_state', GetModelState)

        # create instance to save dataset
        if save_dataset:
            self.save_dataset = SaveDataset(
                f'{seq}', mode='automatic', dbf=dbf, uvl=uvl, model3d_config=model3d_config, fast=fast)

        # define minimum and maximum boundaries
        self.x_min = model3d_config['volume']['position']['xmin']
        self.x_max = model3d_config['volume']['position']['xmax']
        self.y_min = model3d_config['volume']['position']['ymin']
        self.y_max = model3d_config['volume']['position']['ymax']
        self.z_min = model3d_config['volume']['position']['zmin']
        self.z_max = model3d_config['volume']['position']['zmax']

        self.rx_min = model3d_config['volume']['angles']['rxmin']
        self.rx_max = model3d_config['volume']['angles']['rxmax']
        self.ry_min = model3d_config['volume']['angles']['rymin']
        self.ry_max = model3d_config['volume']['angles']['rymax']
        self.rz_min = model3d_config['volume']['angles']['rzmin']
        self.rz_max = model3d_config['volume']['angles']['rzmax']

        # define minimum and maximum light
        self.att_min = model3d_config['light']['att_min']
        self.att_max = model3d_config['light']['att_max']
        self.att_initial = model3d_config['light']['att_initial']

        self.light_names = model3d_config['light']['light_names']

        self.use_collision = model3d_config['collision']['use']
        self.mesh_collision = model3d_config['collision']['mesh']
        self.min_cam_dist = model3d_config['collision']['min_camera_distance']

        # set initial pose
        print('setting initial pose...')
        x = model3d_config['initial_pose']['x']
        y = model3d_config['initial_pose']['y']
        z = model3d_config['initial_pose']['z']
        rx = model3d_config['initial_pose']['rx']
        ry = model3d_config['initial_pose']['ry']
        rz = model3d_config['initial_pose']['rz']
        quaternion = tf.transformations.quaternion_from_euler(rx, ry, rz)
        p = Pose()
        p.position.x = x
        p.position.y = y
        p.position.z = z
        p.orientation.x = quaternion[0]
        p.orientation.y = quaternion[1]
        p.orientation.z = quaternion[2]
        p.orientation.w = quaternion[3]
        self.setPose(p)
        rospy.sleep(1)

    def generateRandomPose(self):

        x = random.uniform(self.x_min, self.x_max)
        y = random.uniform(self.y_min, self.y_max)
        z = random.uniform(self.z_min, self.z_max)

        rx = random.uniform(self.rx_min, self.rx_max)
        ry = random.uniform(self.ry_min, self.ry_max)
        rz = random.uniform(self.rz_min, self.rz_max)

        quaternion = tf.transformations.quaternion_from_euler(rx, ry, rz)

        p = Pose()
        p.position.x = x
        p.position.y = y
        p.position.z = z

        p.orientation.x = quaternion[0]
        p.orientation.y = quaternion[1]
        p.orientation.z = quaternion[2]
        p.orientation.w = quaternion[3]

        return p

    def generatePath(self, final_pose=None):

        initial_pose = self.getPose().pose

        if final_pose == None:
            final_pose = self.generateRandomPose()

            while True:
                xyz_initial = np.array(
                    [initial_pose.position.x, initial_pose.position.y, initial_pose.position.z])
                xyz_final = np.array(
                    [final_pose.position.x, final_pose.position.y, final_pose.position.z])
                l2_dst = np.linalg.norm(xyz_final - xyz_initial)

                # if final pose is close to the initial or there is collision, choose another final pose
                if l2_dst < 1.5 or self.checkCollision(initial_pose=initial_pose, final_pose=final_pose):
                    final_pose = self.generateRandomPose()
                else:
                    break

        # compute n_steps based on l2_dist
        n_steps = int(l2_dst / self.dbf)

        print('using n_steps of: ', n_steps)

        step_poses = []  # list of tuples
        rx, ry, rz = tf.transformations.euler_from_quaternion(
            [initial_pose.orientation.x, initial_pose.orientation.y, initial_pose.orientation.z, initial_pose.orientation.w])
        pose_initial_dct = {'x': initial_pose.position.x,
                            'y': initial_pose.position.y,
                            'z': initial_pose.position.z,
                            'rx': rx,
                            'ry': ry,
                            'rz': rz}

        rx, ry, rz = tf.transformations.euler_from_quaternion(
            [final_pose.orientation.x, final_pose.orientation.y, final_pose.orientation.z, final_pose.orientation.w])
        pose_final_dct = {'x': final_pose.position.x,
                          'y': final_pose.position.y,
                          'z': final_pose.position.z,
                          'rx': rx,
                          'ry': ry,
                          'rz': rz}

        x_step_var = (pose_final_dct['x'] - pose_initial_dct['x']) / n_steps
        y_step_var = (pose_final_dct['y'] - pose_initial_dct['y']) / n_steps
        z_step_var = (pose_final_dct['z'] - pose_initial_dct['z']) / n_steps
        rx_step_var = (pose_final_dct['rx'] - pose_initial_dct['rx']) / n_steps
        ry_step_var = (pose_final_dct['ry'] - pose_initial_dct['ry']) / n_steps
        rz_step_var = (pose_final_dct['rz'] - pose_initial_dct['rz']) / n_steps

        for i in range(n_steps):
            dct = {'x': pose_initial_dct['x'] + (i + 1) * x_step_var,
                   'y': pose_initial_dct['y'] + (i + 1) * y_step_var,
                   'z': pose_initial_dct['z'] + (i + 1) * z_step_var,
                   'rx': pose_initial_dct['rx'] + (i + 1) * rx_step_var,
                   'ry': pose_initial_dct['ry'] + (i + 1) * ry_step_var,
                   'rz': pose_initial_dct['rz'] + (i + 1) * rz_step_var}
            pose = data2pose(dct)
            step_poses.append(pose)

        return step_poses

    def getPose(self):
        return self.get_model_state_service(self.model_name, 'world')

    def setPose(self, pose):

        req = SetModelStateRequest()  # Create an object of type SetModelStateRequest
        req.model_state.model_name = self.model_name

        req.model_state.pose.position.x = pose.position.x
        req.model_state.pose.position.y = pose.position.y
        req.model_state.pose.position.z = pose.position.z
        req.model_state.pose.orientation.x = pose.orientation.x
        req.model_state.pose.orientation.y = pose.orientation.y
        req.model_state.pose.orientation.z = pose.orientation.z
        req.model_state.pose.orientation.w = pose.orientation.w
        req.model_state.reference_frame = 'world'

        self.set_state_service(req.model_state)

    def generateLights(self, n_steps, random):
        lights = []
        if random:
            lights = [np.random.uniform(
                low=self.att_min, high=self.att_max) for _ in range(n_steps)]
        else:
            initial_light = self.att_initial
            final_light = np.random.uniform(
                low=self.att_min, high=self.att_max)

            step_light = (final_light - initial_light) / n_steps

            for i in range(n_steps):
                lights.append(initial_light + (i + 1) * step_light)

            self.att_initial = final_light
        return lights

    def setLight(self, light):

        for name in self.light_names:

            my_str = f'name: "{name}" \nattenuation_quadratic: {light}'

            with open('/tmp/set_light.txt', 'w') as f:
                f.write(my_str)

            os.system(
                f'gz topic -p /gazebo/my_room_024/light/modify -f /tmp/set_light.txt')

    def checkCollision(self, initial_pose, final_pose):
        if self.use_collision is False:
            print('not using COLLISIONS.')
            return False
        # load mesh
        mesh = trimesh.load(
            '/home/danc/models_3d/santuario_collision/Virtudes_Chapel.dae', force='mesh')

        initial_pose.position.x

        p1_xyz = np.array(
            [initial_pose.position.x, initial_pose.position.y, initial_pose.position.z])
        p1_quat = np.array([initial_pose.orientation.x, initial_pose.orientation.y,
                           initial_pose.orientation.z, initial_pose.orientation.w])

        p2_xyz = np.array(
            [final_pose.position.x, final_pose.position.y, final_pose.position.z])
        p2_quat = np.array([final_pose.orientation.x, final_pose.orientation.y,
                           final_pose.orientation.z, final_pose.orientation.w])

        dist_p1_to_p2 = np.linalg.norm(p2_xyz-p1_xyz)

        print(
            f' {Fore.BLUE} Checking collision... {Fore.RESET} between {p1_xyz} and {p2_xyz}')

        orientation = p2_xyz - p1_xyz
        norm_orientation = np.linalg.norm(orientation)
        orientation = orientation / norm_orientation

        ray_origins = np.array([p1_xyz])
        ray_directions = np.array([orientation])

        collisions, _, _ = mesh.ray.intersects_location(
            ray_origins=ray_origins,
            ray_directions=ray_directions)

        closest_collision_to_p1 = self.getClosestCollision(collisions, p1_xyz)

        # compare the closest collision with the position of p2
        if closest_collision_to_p1 < dist_p1_to_p2:
            # collision
            print(f'{Fore.RED} Collision Detected. {Fore.RESET}')
            return True
        else:
            # no collision

            # check if p2 camera viewpoint if close to a obstacle.
            orientation = R.from_quat(p2_quat).as_matrix()[:, 0]
            norm_orientation = np.linalg.norm(orientation)
            orientation = orientation / norm_orientation

            ray_origins = np.array([p2_xyz])
            ray_directions = np.array([orientation])

            collisions, _, _ = mesh.ray.intersects_location(
                ray_origins=ray_origins,
                ray_directions=ray_directions)

            closest_collision_to_p2 = self.getClosestCollision(
                collisions, p2_xyz)

            if closest_collision_to_p2 < self.min_cam_dist:

                print(
                    f'{Fore.YELLOW} Final Pose is too close to a obstacle. {Fore.RESET}')
                return True
            else:
                print(f'{Fore.GREEN} NO Collision Detected {Fore.RESET}')
                return False

    def checkCollisionVis(self, initial_pose, final_pose):
        if self.use_collision is False:
            print('not using COLLISIONS.')
            return False
        # load mesh
        #TODO #83 this should not be hardcoded
        mesh = trimesh.load(
            '/home/danc/models_3d/santuario_collision/Virtudes_Chapel.dae', force='mesh')

        initial_pose.position.x

        p1_xyz = np.array(
            [initial_pose.position.x, initial_pose.position.y, initial_pose.position.z])
        p1_quat = np.array([initial_pose.orientation.x, initial_pose.orientation.y,
                           initial_pose.orientation.z, initial_pose.orientation.w])

        p2_xyz = np.array(
            [final_pose.position.x, final_pose.position.y, final_pose.position.z])
        p2_quat = np.array([final_pose.orientation.x, final_pose.orientation.y,
                           final_pose.orientation.z, final_pose.orientation.w])

        dist_p1_to_p2 = np.linalg.norm(p2_xyz-p1_xyz)

        print(
            f' {Fore.BLUE} Checking collision... {Fore.RESET} between {p1_xyz} and {p2_xyz}')

        orientation = p2_xyz - p1_xyz
        norm_orientation = np.linalg.norm(orientation)
        orientation = orientation / norm_orientation

        ray_origins = np.array([p1_xyz])
        ray_directions = np.array([orientation])

        collisions, _, _ = mesh.ray.intersects_location(
            ray_origins=ray_origins,
            ray_directions=ray_directions)

        closest_collision_to_p1 = self.getClosestCollision(collisions, p1_xyz)

        # compare the closest collision with the position of p2
        if closest_collision_to_p1 < dist_p1_to_p2:
            # collision
            print(f'{Fore.RED} Collision Detected. {Fore.RESET}')
            return 1
        else:
            # no collision

            # check if p2 camera viewpoint if close to a obstacle.
            orientation = R.from_quat(p2_quat).as_matrix()[:, 0]
            norm_orientation = np.linalg.norm(orientation)
            orientation = orientation / norm_orientation

            ray_origins = np.array([p2_xyz])
            ray_directions = np.array([orientation])

            collisions, _, _ = mesh.ray.intersects_location(
                ray_origins=ray_origins,
                ray_directions=ray_directions)

            closest_collision_to_p2 = self.getClosestCollision(
                collisions, p2_xyz)

            if closest_collision_to_p2 < self.min_cam_dist:

                print(
                    f'{Fore.YELLOW} Final Pose is too close to a obstacle. {Fore.RESET}')
                return 0.5
            else:
                print(f'{Fore.GREEN} NO Collision Detected {Fore.RESET}')
                return 0

    def generatePathViz(self, final_pose):

        initial_pose = self.getPose().pose

        xyz_initial = np.array(
            [initial_pose.position.x, initial_pose.position.y, initial_pose.position.z])
        xyz_final = np.array(
            [final_pose.position.x, final_pose.position.y, final_pose.position.z])
        l2_dst = np.linalg.norm(xyz_final - xyz_initial)

        # compute n_steps based on l2_dist
        n_steps = int(l2_dst / self.dbf)

        print('using n_steps of: ', n_steps)

        step_poses = []  # list of tuples
        rx, ry, rz = tf.transformations.euler_from_quaternion(
            [initial_pose.orientation.x, initial_pose.orientation.y, initial_pose.orientation.z, initial_pose.orientation.w])
        pose_initial_dct = {'x': initial_pose.position.x,
                            'y': initial_pose.position.y,
                            'z': initial_pose.position.z,
                            'rx': rx,
                            'ry': ry,
                            'rz': rz}

        rx, ry, rz = tf.transformations.euler_from_quaternion(
            [final_pose.orientation.x, final_pose.orientation.y, final_pose.orientation.z, final_pose.orientation.w])
        pose_final_dct = {'x': final_pose.position.x,
                          'y': final_pose.position.y,
                          'z': final_pose.position.z,
                          'rx': rx,
                          'ry': ry,
                          'rz': rz}

        x_step_var = (pose_final_dct['x'] - pose_initial_dct['x']) / n_steps
        y_step_var = (pose_final_dct['y'] - pose_initial_dct['y']) / n_steps
        z_step_var = (pose_final_dct['z'] - pose_initial_dct['z']) / n_steps
        rx_step_var = (pose_final_dct['rx'] - pose_initial_dct['rx']) / n_steps
        ry_step_var = (pose_final_dct['ry'] - pose_initial_dct['ry']) / n_steps
        rz_step_var = (pose_final_dct['rz'] - pose_initial_dct['rz']) / n_steps

        for i in range(n_steps):
            dct = {'x': pose_initial_dct['x'] + (i + 1) * x_step_var,
                   'y': pose_initial_dct['y'] + (i + 1) * y_step_var,
                   'z': pose_initial_dct['z'] + (i + 1) * z_step_var,
                   'rx': pose_initial_dct['rx'] + (i + 1) * rx_step_var,
                   'ry': pose_initial_dct['ry'] + (i + 1) * ry_step_var,
                   'rz': pose_initial_dct['rz'] + (i + 1) * rz_step_var}
            pose = data2pose(dct)
            step_poses.append(pose)

        return step_poses

    def getClosestCollision(self, collisions, p1_xyz):
        closest_collision_to_p1 = np.inf

        for position_collision in collisions:
            dist_collision_p1 = np.linalg.norm(position_collision - p1_xyz)
            if dist_collision_p1 < closest_collision_to_p1:
                closest_collision_to_p1 = dist_collision_p1
        return closest_collision_to_p1

    def saveFrame(self):
        self.save_dataset.saveFrame()

    def getFrameIdx(self):
        return self.save_dataset.frame_idx
