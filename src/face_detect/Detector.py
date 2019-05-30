# -*- coding: utf-8 -*-
###############################################
#created by :  lxy
#Time:  2019/04/22 14:09
#project: Face recognize
#company: 
#rversion: 0.1
#tool:   python 2.7
#modified:
#description  test caffe model and figure confusionMatrix
####################################################
import os
import sys
import mxnet as mx
import numpy as np
import math
import cv2
import time
from tools_mxnet import nms, adjust_input,detect_first_stage
sys.path.append(os.path.join(os.path.dirname(__file__),'../configs'))
from config import cfgs

class MtcnnDetector(object):
    """
        Joint Face Detection and Alignment using Multi-task Cascaded Convolutional Neural Networks
        see https://github.com/kpzhang93/MTCNN_face_detection_alignment
        this is a mxnet version
    """
    def __init__(self, minsize = 24,threshold = [0.6, 0.7, 0.8],model_folder='../models/mtcnn_mxnet/', \
                factor = 0.709,num_worker = 1,accurate_landmark = False,ctx=mx.cpu()):
        """
            Parameters:
                model_folder : string
                    path for the models
                minsize : float number
                    minimal face to detect
                threshold : float number
                    detect threshold for 3 stages
                factor: float number
                    scale factor for image pyramid
                num_worker: int number
                    number of processes we use for first stage
                accurate_landmark: bool
                    use accurate landmark localization or not
        """
        self.num_worker = num_worker
        self.accurate_landmark = accurate_landmark
        # load 4 models from folder
        models = ['det1','det2', 'det3','det4']
        models = [os.path.join(model_folder, f) for f in models]  
        self.PNet = mx.model.FeedForward.load(models[0], 1, ctx=ctx)
        self.RNet = mx.model.FeedForward.load(models[1], 1, ctx=ctx)
        self.ONet = mx.model.FeedForward.load(models[2], 1, ctx=ctx)
        self.minsize   = float(minsize)
        self.factor    = float(factor)
        self.threshold = threshold

    def convert_to_square(self, bbox):
        """
            convert bbox to square
        Parameters:
            bbox: numpy array, shape n x 5 input bbox
        Returns: square bbox
        """
        square_bbox = bbox.copy()
        h = bbox[:, 3] - bbox[:, 1] + 1
        w = bbox[:, 2] - bbox[:, 0] + 1
        max_side = np.maximum(h,w)
        square_bbox[:, 0] = bbox[:, 0] + w*0.5 - max_side*0.5
        square_bbox[:, 1] = bbox[:, 1] + h*0.5 - max_side*0.5
        square_bbox[:, 2] = square_bbox[:, 0] + max_side - 1
        square_bbox[:, 3] = square_bbox[:, 1] + max_side - 1
        return square_bbox

    def calibrate_box(self, bbox, reg):
        """
            calibrate bboxes
        Parameters:
            bbox: numpy array, shape n x 5
                input bboxes
            reg:  numpy array, shape n x 4
                bboxex adjustment
        Returns:
            bboxes after refinement
        """
        w = bbox[:, 2] - bbox[:, 0] + 1
        w = np.expand_dims(w, 1)
        h = bbox[:, 3] - bbox[:, 1] + 1
        h = np.expand_dims(h, 1)
        reg_m = np.hstack([w, h, w, h])
        aug = reg_m * reg
        bbox[:, 0:4] = bbox[:, 0:4] + aug
        return bbox

 
    def pad(self, bboxes, w, h):
        """
            pad the the bboxes, alse restrict the size of it
        Parameters:
            bboxes: numpy array, n x 5
            w: float number width of the input image
            h: float number height of the input image
        Returns :
            dy, dx : numpy array, n x 1 ,start point of the bbox in target image
            edy, edx : numpy array, n x 1 ,end point of the bbox in target image
            y, x : numpy array, n x 1 ,start point of the bbox in original image
            ex, ex : numpy array, n x 1 ,end point of the bbox in original image
            tmph, tmpw: numpy array, n x 1, height and width of the bbox
        """
        tmpw, tmph = bboxes[:, 2] - bboxes[:, 0] + 1,  bboxes[:, 3] - bboxes[:, 1] + 1
        num_box = bboxes.shape[0]
        dx , dy= np.zeros((num_box, )), np.zeros((num_box, ))
        edx, edy  = tmpw.copy()-1, tmph.copy()-1
        x, y, ex, ey = bboxes[:, 0], bboxes[:, 1], bboxes[:, 2], bboxes[:, 3]
        tmp_index = np.where(ex > w-1)
        edx[tmp_index] = tmpw[tmp_index] + w - 2 - ex[tmp_index]
        ex[tmp_index] = w - 1
        tmp_index = np.where(ey > h-1)
        edy[tmp_index] = tmph[tmp_index] + h - 2 - ey[tmp_index]
        ey[tmp_index] = h - 1
        tmp_index = np.where(x < 0)
        dx[tmp_index] = 0 - x[tmp_index]
        x[tmp_index] = 0
        tmp_index = np.where(y < 0)
        dy[tmp_index] = 0 - y[tmp_index]
        y[tmp_index] = 0
        return_list = [dy, edy, dx, edx, y, ey, x, ex, tmpw, tmph]
        return_list = [item.astype(np.int32) for item in return_list]
        return  return_list

    def slice_index(self, number):
        """
            slice the index into (n,n,m), m < n
        Parameters:
        ----------
            number: int number
        """
        def chunks(l, n):
            """Yield successive n-sized chunks from l."""
            for i in range(0, len(l), n):
                yield l[i:i + n]
        num_list = range(number)
        return list(chunks(num_list, self.num_worker))

    def Pnet_Process(self,img):
        '''
        input: mx_img, bgr order of shape (1, 3, n, m)
        return: rectangles, [x1,y1,x2,y2,score]
        '''
        MIN_DET_SIZE = 12
        # detected boxes
        total_boxes = []
        height, width, _ = img.shape
        minl = min(height, width)
        # get all the valid scales
        scales = []
        m = MIN_DET_SIZE/self.minsize
        minl *= m
        factor_count = 0
        while minl > MIN_DET_SIZE:
            scales.append(m*self.factor**factor_count)
            minl *= self.factor
            factor_count += 1
        # first stage   
        for s in scales:
            local_boxes = detect_first_stage(img, self.PNet, s, self.threshold[0])
            if len(local_boxes) == 0:
                continue
            total_boxes.extend(local_boxes)
        # remove the Nones 
        total_boxes = [i for i in total_boxes if i is not None]
        if len(total_boxes) == 0:
            return []
        total_boxes = np.vstack(total_boxes)
        if total_boxes.size == 0:
            return []
        # merge the detection from first stage
        pick = nms(total_boxes[:, 0:5], 0.5, 'Union')
        total_boxes = total_boxes[pick]
        bbw = total_boxes[:, 2] - total_boxes[:, 0] + 1
        bbh = total_boxes[:, 3] - total_boxes[:, 1] + 1
        # refine the bboxes
        total_boxes = np.vstack([total_boxes[:, 0]+total_boxes[:, 5] * bbw,
                                 total_boxes[:, 1]+total_boxes[:, 6] * bbh,
                                 total_boxes[:, 2]+total_boxes[:, 7] * bbw,
                                 total_boxes[:, 3]+total_boxes[:, 8] * bbh,
                                 total_boxes[:, 4]
                                 ])
        total_boxes = total_boxes.T
        total_boxes = self.convert_to_square(total_boxes)
        total_boxes[:, 0:4] = np.round(total_boxes[:, 0:4])
        return total_boxes

    def Rnet_Process(self,total_boxes,img):
        '''
        total_boxes: [[x1,y1,x2,y2,score]]
        return: rectangles [[x1,y1,x2,y2,score]]
        '''
        height, width, _ = img.shape
        num_box = total_boxes.shape[0]
        # pad the bbox
        [dy, edy, dx, edx, y, ey, x, ex, tmpw, tmph] = self.pad(total_boxes, width, height)
        # (3, 24, 24) is the input shape for RNet
        input_buf = np.zeros((num_box, 3, 24, 24), dtype=np.float32)
        for i in range(num_box):
            tmp = np.zeros((tmph[i], tmpw[i], 3), dtype=np.uint8)
            tmp[dy[i]:edy[i]+1, dx[i]:edx[i]+1, :] = img[y[i]:ey[i]+1, x[i]:ex[i]+1, :]
            input_buf[i, :, :, :] = adjust_input(cv2.resize(tmp, (24, 24)))
        output = self.RNet.predict(input_buf)
        # filter the total_boxes with threshold
        passed = np.where(output[1][:, 1] > self.threshold[1])
        total_boxes = total_boxes[passed]
        if total_boxes.size == 0:
            return []
        total_boxes[:, 4] = output[1][passed, 1].reshape((-1,))
        reg = output[0][passed]
        # nms
        pick = nms(total_boxes, 0.7, 'Union')
        total_boxes = total_boxes[pick]
        total_boxes = self.calibrate_box(total_boxes, reg[pick])
        total_boxes = self.convert_to_square(total_boxes)
        total_boxes[:, 0:4] = np.round(total_boxes[:, 0:4])
        return total_boxes

    def Onet_Process(self,total_boxes,img):
        '''
        total_boxes: [[x1,y1,x2,y2,score]]
        return: rectangles [[x1,y1,x2,y2,score]]
        '''
        num_box = total_boxes.shape[0]
        height, width, _ = img.shape
        # pad the bbox
        [dy, edy, dx, edx, y, ey, x, ex, tmpw, tmph] = self.pad(total_boxes, width, height)
        # (3, 48, 48) is the input shape for ONet
        input_buf = np.zeros((num_box, 3, 48, 48), dtype=np.float32)
        for i in range(num_box):
            tmp = np.zeros((tmph[i], tmpw[i], 3), dtype=np.float32)
            tmp[dy[i]:edy[i]+1, dx[i]:edx[i]+1, :] = img[y[i]:ey[i]+1, x[i]:ex[i]+1, :]
            input_buf[i, :, :, :] = adjust_input(cv2.resize(tmp, (48, 48)))
        output = self.ONet.predict(input_buf)
        # filter the total_boxes with threshold
        passed = np.where(output[2][:, 1] > self.threshold[2])
        total_boxes = total_boxes[passed]
        if total_boxes.size == 0:
            return []
        total_boxes[:, 4] = output[2][passed, 1].reshape((-1,))
        reg = output[1][passed]
        points = output[0][passed]
        # compute landmark points
        bbw = total_boxes[:, 2] - total_boxes[:, 0] + 1
        bbh = total_boxes[:, 3] - total_boxes[:, 1] + 1
        points[:, 0:5] = np.expand_dims(total_boxes[:, 0], 1) + np.expand_dims(bbw, 1) * points[:, 0:5]
        points[:, 5:10] = np.expand_dims(total_boxes[:, 1], 1) + np.expand_dims(bbh, 1) * points[:, 5:10]
        # nms
        total_boxes = self.calibrate_box(total_boxes, reg)
        pick = nms(total_boxes, 0.3, 'Min')
        total_boxes = total_boxes[pick]
        points = points[pick]
        points_xy = []
        for j in range(5):
            points_xy.append(points[:,j])
            points_xy.append(points[:,j+5])
        points_xy = np.array(points_xy)
        if cfgs.x_y:
            rectangles = np.concatenate((total_boxes,points_xy.T),axis=1)
        else:
            rectangles = np.concatenate((total_boxes,points),axis=1)
        return rectangles

    def detectFace(self, img):
        """
            detect face over img
        Parameters:
            img: numpy array, bgr order of shape (1, 3, n, m)
        Retures:
            bboxes: numpy array, n x 5 (x1,y2,x2,y2,score)
            points: numpy array, n x 10 (x1, x2 ... x5, y1, y2 ..y5)
        """
        # check input
        if img is None:
            return []
        # only works for color image
        if len(img.shape) != 3:
            return []
        t = time.time()
        #first stage
        rectangles = self.Pnet_Process(img)
        if len(rectangles) ==0:
            return []
        t1 = time.time() - t
        t = time.time()
        # second stage
        rectangles = self.Rnet_Process(rectangles,img)
        if len(rectangles)==0:
            return []
        t2 = time.time() - t 
        t = time.time()
        # third stage
        rectangles = self.Onet_Process(rectangles,img)
        t3 = time.time() - t
        if cfgs.time:
            print("time cost " + '{:.3f}'.format(t1+t2+t3) + '  pnet {:.3f}  rnet {:.3f}  onet{:.3f}'.format(t1, t2,t3))
        return rectangles

