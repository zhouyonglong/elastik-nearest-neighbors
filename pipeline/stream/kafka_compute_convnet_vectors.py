from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from kafka import KafkaConsumer, KafkaProducer
from io import BytesIO
from multiprocessing import Pool, cpu_count
from scipy.misc import imread, imsave
from lycon import resize
from time import time
from tqdm import tqdm
import boto3
import json
import numpy as np
import pdb
import sys

from keras.models import Model
from keras.applications import MobileNet
from keras.applications.imagenet_utils import preprocess_input, decode_predictions


class Convnet(object):

    def __init__(self):
        self.preprocess_mode = 'tf'
        model = MobileNet(weights='imagenet')
        self.model = Model(model.input, [model.output, model.get_layer('conv_preds').output])
    
    def get_labels_and_vecs(self, imgs_iter):

        imgs = np.array(imgs_iter)
        imgs = preprocess_input(imgs.astype(np.float32), mode=self.preprocess_mode)

        clsf, vecs = self.model.predict(imgs)
        labels = [' '.join([y[1].lower() for y in x]) for x in decode_predictions(clsf, top=10)]
        vecs = np.squeeze(vecs)

        return labels, vecs

def _preprocess_img(img_bytes):
    # Read from bytes to numpy array.
    img = imread(img_bytes)

    # Extremely fast resize.
    img = resize(img, 224, 224, interpolation=0)
    
    # Regular image: return.
    if img.shape[-1] == 3:
        return img
    
    # Grayscale image: repeat up to 3 channels.
    elif len(img.shape) == 2:
        return np.repeat(img[:, :, np.newaxis], 3, -1)

    # Other image: repeat first channel 3 times.
    return np.repeat(img[:, :, :1], 3, -1)

def _get_img_bytes_from_s3(args):
    bucket, key, s3_client = args
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    return BytesIO(obj['Body'].read())

if __name__ == "__main__":

    ap = ArgumentParser(description="See script header")
    ap.add_argument("--kafka_sub_topic", 
                    help="Name of topic from which images are consumed", 
                    default="aknn-demo.image-objects")
    ap.add_argument("--kafka_pub_topic", 
                    help="Name of topic to which feature vectors get produced", 
                    default="aknn-demo.convnet-features")
    ap.add_argument("--kafka_servers", 
                    help="Bootstrap servers for Kafka",
                    default="ip-172-31-19-114.ec2.internal:9092,ip-172-31-18-192.ec2.internal:9092,ip-172-31-20-205.ec2.internal:9092")
    ap.add_argument("--kafka_group",
                    help="Group ID for Kafka consumer", 
                    default="aknn-demo.comput-convnet-features")
    ap.add_argument("-b", "--batch_size", type=int, default=100)

    args = vars(ap.parse_args())

    consumer = KafkaConsumer(
        args["kafka_sub_topic"],
        bootstrap_servers=args["kafka_servers"],
        group_id=args["kafka_group"],
        auto_offset_reset="earliest",
        key_deserializer=lambda k: k.decode(),
        value_deserializer=lambda v: json.loads(v.decode())
    )
    
    s3_client = boto3.client('s3')

#    producer = KafkaProducer(bootstrap_servers=",".join(KAFKA_SERVERS))
    convnet = Convnet()
    pool = Pool(cpu_count())
    tpex = ThreadPoolExecutor(max_workers=12)

    for msg in consumer:
    
        print("%d images in batch" % len(msg.value))
        T0 = time()

        t0 = time()
        data = map(lambda o: (o['bucket'], o['key'], s3_client), msg.value)
        imgs_bytes = list(tpex.map(_get_img_bytes_from_s3, data))
        print("Download images", time() - t0)
        
        t0 = time()
        imgs_iter = pool.map(_preprocess_img, imgs_bytes)
        print("Preprocess images", time() - t0)

        t0 = time()
        labels, vecs = convnet.get_labels_and_vecs(imgs_iter)
        print("Compute", time() - t0)
        
        print("%s: %s %.2lf, %.2lf, %d" \
            % (msg.key, str(vecs.shape), vecs.mean(), vecs.std(), time() - T0))

        #np.save("/home/ubuntu/tmp/%s.npy" % msg.key.split('.')[0], vecs.astype(np.float16))
        #with open("/home/ubuntu/tmp/%s.txt" % msg.key.split('.')[0],"w") as fp:
        #    fp.write("\n".join([m.key for m in batch]))
        
        #batch = []
