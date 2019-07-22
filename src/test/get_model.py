# -*- coding: utf-8 -*-
###############################################
#created by :  lxy
#Time:  2019/01/16 14:09
#project: Face recognize
#company: Senscape
#rversion: 0.1
#tool:   python 2.7
#modified:
#description  
####################################################
import sys
import os
os.environ['GLOG_minloglevel'] = '2'
import cv2
import numpy as np
import time
import mxnet as mx
from mxnet import nd
sys.path.append(os.path.join(os.path.dirname(__file__),'../configs'))
from config import cfgs
sys.path.append(os.path.join(os.path.dirname(__file__),'../utils'))
from util import Img_Pad


class Face_Anti_Spoof(object):
    def __init__(self,model_path,epoch_num,img_size,process=False,layer=None,ctx=mx.cpu(0)):
        self.process = process
        if cfgs.mx_version:
            self.model_net = self.load_model2(ctx,img_size,model_path,epoch_num,layer)
        else:
            self.load_model(model_path,epoch_num,ctx)
        self.h, self.w = img_size

    def load_model(self,model_path,epoch_num,ctx):
        self.model_net = mx.model.FeedForward.load(model_path,epoch_num,ctx=ctx)

    def display_model(self,sym):
        data_shape = {"data":(1,3,112,112)}
        net_show = mx.viz.plot_network(symbol=sym,shape=data_shape)  
        net_show.render(filename="mxnet_rnet",cleanup=True)

    def load_model2(self,ctx, image_size, prefix,epoch_num,layer):
        print('loading',prefix, epoch_num)
        sym, arg_params, aux_params = mx.model.load_checkpoint(prefix, epoch_num)
        if layer is not None:
            all_layers = sym.get_internals()
            if cfgs.debug:
                print("all layers ",all_layers.list_outputs()[-3:])
            sym = all_layers[layer+'_output']
        if cfgs.display_model:
            self.display_model(sym)
        model = mx.mod.Module(symbol=sym, context=ctx, data_names=('data',),label_names = None)
        if cfgs.batch_use and self.process:
            model.bind(data_shapes=[('data', (cfgs.batch_size, 3, image_size[0], image_size[1]))])
        else:
            model.bind(data_shapes=[('data', (1, 3, int(image_size[0]), int(image_size[1])))],for_training=False)
        model.set_params(arg_params, aux_params)
        if cfgs.model_resave:
            print(">>> begin to resave model")
            dellist = []
            for k,v in arg_params.iteritems():
                if k.startswith('fc'):
                    dellist.append(k)
                elif k.startswith('Drop'):
                    dellist.append(k)
                elif k.startswith('softmax'):
                    dellist.append(k)
                if cfgs.debug:
                    print("key name: ",k)
            for d in dellist:
                del arg_params[d]
            mx.model.save_checkpoint(prefix+"resave", 0, sym, arg_params, aux_params)
            print(">>> resave model is over")
        return model

    def process_img(self,imgs):
        img_out = []
        for img in imgs:
            if cfgs.IMG_NORM:
                img = img / 255.0
                img[:,:,0] -= 0.485
                img[:,:,0] = img[:,:,0] / 0.229 
                img[:,:,1] -= 0.456
                img[:,:,1] = img[:,:,1] / 0.224
                img[:,:,2] -= 0.406
                img[:,:,2] = img[:,:,2] / 0.225
            img = np.transpose(img,(2,0,1))
            img_out.append(img)
        return np.array(img_out)

    def get_softmax_cls(self,features):
        features = mx.nd.array(features)
        probility = mx.ndarray.softmax(features,axis=1)
        probility = probility.asnumpy()
        class_num = np.argmax(probility,axis=1)
        return probility,class_num

    def get_sigmoid_cls(self,features):
        pred_data = mx.nd.array(features)
        prediction = nd.broadcast_greater(pred_data,nd.array([0.5]))
        return prediction.asnumpy()

    def inference(self,img):
        img_input = self.process_img(img)
        t = time.time()
        if cfgs.mx_version:
            data = mx.nd.array(img_input)
            db = mx.io.DataBatch(data=(data,))
            self.model_net.forward(db, is_train=False)
            features = self.model_net.get_outputs()[0].asnumpy()
        else: 
            features = self.model_net.predict(img_input)
            #embedding = features[0]
        t1 = time.time() - t
        if cfgs.time:
            print("mxnet forward time cost: ",t1)
        if cfgs.debug:
            print("feature shape ",np.shape(features))
        #print("features ",features[0,:5])
        #probility,class_num = self.get_softmax_cls(features)
        class_num  = self.get_sigmoid_cls(features)
        return features,class_num

if __name__ == '__main__':
    imgpath = "/home/lxy/Develop/Center_Loss/mtcnn-caffe/image/pics/test.jpg"
    img = cv2.imread(imgpath)
    print("org",img.shape)
    size_ = [112,112]
    img_o = Img_Pad(img,size_)
    print("out",img_o.shape)
    cv2.imshow("img",img_o)
    cv2.imshow("org",img)
    cv2.waitKey(0)