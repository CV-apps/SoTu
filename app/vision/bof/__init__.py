# -*- coding: utf-8 -*-

import os
import pickle

import cv2
import numpy as np
from flask import current_app
from scipy.cluster.vq import kmeans, vq
from sklearn.preprocessing import normalize

from . import he, sift


def extract(uris):
    n_uris = len(uris)
    print("Get sift features of %d images" % n_uris)
    images = [cv2.imread(os.path.join(current_app.config['DATA_DIR'], uri))
              for uri in uris]
    # 获取每幅图的所有keypoint和对应的descriptor
    keypoints, descriptors = sift.extract_all(images)
    # 垂直堆叠所有的descriptor，每个128维
    des_all = np.vstack(descriptors)

    k = 1000  # TODO: 选择合适的聚类数
    print("Start kmeans with %d descriptors and %d centroids" %
          (len(des_all), k))
    # 对descriptors进行kmeans聚类，生成k个聚类中心
    centroids = kmeans(des_all, k, 1)[0]

    # 映射每幅图的每个descriptor到距其最近的聚类并得到该聚类的索引
    labels = [vq(des, centroids)[0] for des in descriptors]
    # 根据投影矩阵对每幅图的所有descriptor降维
    projections = [np.dot(des, P.T) for des in descriptors]
    # 得到k*64的中值矩阵
    medians = he.get_medians(np.vstack(projections), np.hstack(labels), k)
    # 得到每幅图的每个descriptor对应的Hamming编码
    binaries = [he.get_binary(prj, lbl, medians)
                for prj, lbl in zip(projections, labels)]

    # 获取每个聚类的Entry，包含centroid，median和所有属于这个聚类的Hamming编码及图片索引
    entries = [Entry(med) for med in medians]
    for i, (binary, label) in enumerate(zip(binaries, labels)):
        for b, l in zip(binary, label):
            entries[l].add_signature(b, i)

    # 统计每幅图所有descriptor所属聚类的频率向量
    freqs = np.array([np.bincount(lbl, minlength=k) for lbl in labels])
    # 计算聚类频率矩阵的idf(sklearn的实现方式)
    idf = np.log((freqs.shape[0] + 1) / (np.sum((freqs > 0), axis=0) + 1)) + 1

    bof_path = os.path.join(current_app.config['FEATURE_DIR'], 'bof.pkl')
    with open(bof_path, 'wb') as bof_pkl:
        pickle.dump((uris, labels, idf, centroids, P, entries),
                    bof_pkl)


def match(uri, top_k=20):
    bof_path = os.path.join(current_app.config['FEATURE_DIR'], 'bof.pkl')
    bof_pkl = open(bof_path, 'rb')
    uris, labels, idf, centroids, P, entries = pickle.load(bof_pkl)

    k = len(centroids)
    img = cv2.imread(os.path.join(current_app.config['DATA_DIR'], uri))
    # 计算要搜索的图像的所有descriptor
    des = sift.extract(img)[1]
    # 根据投影矩阵对要搜索的图像的所有descriptor降维
    prj = np.dot(des, P.T)
    # 映射要搜索的图像的所有descriptor到距其最近的聚类并得到该聚类的索引
    lbl = vq(des, centroids)[0]
    # 计算要搜索的图像的所有descriptor对应的Hamming编码
    binary = he.get_binary(prj, lbl, medians)

    # 定义Hamming阈值
    threshold = 25
    print("Start to score all iamges")
    scores = np.zeros([len(des), len(uris)])
    for i, (b, l) in enumerate(binary, lbl):
        for b2, idx in entries[l]:
            if he.ham_dist(b, b2) < threshold:
                scores[i][idx] += 1
    weighted = sum([idf[l] * s for l, s in zip(lbl, scores)])

    rank = np.argsort(-scores)[: top_k]
    images = [uris[r] for r in rank]
    return images
