import math
import os
from glob import glob, iglob
from urllib.request import urlopen

import cv2
import numpy as np
from facenet_sandberg import facenet
from facenet_sandberg.common_types import *
from skimage import transform as trans
from sklearn.metrics.pairwise import paired_distances


def normalize_image(image: Image) -> Image:
    mean = np.mean(image)
    std = np.std(image)
    std_adj = np.maximum(std, 1.0 / np.sqrt(image.size))
    y = np.multiply(np.subtract(image, mean), 1 / std_adj)
    return y


def fixed_standardize(image: Image) -> Image:
    image = image - 127.5
    image = image / 128.0
    return image


def fix_image(image: Image) -> Image:
    if image.ndim < 2:
        image = image[:, :, np.newaxis]
    if image.ndim == 2:
        image = add_color(image)
    image = image[:, :, 0:3]
    return image


def add_color(image: Image) -> Image:
    w, h = image.shape
    ret = np.empty((w, h, 3), dtype=np.uint8)
    ret[:, :, 0] = ret[:, :, 1] = ret[:, :, 2] = image
    return ret


def fix_mtcnn_bb(max_y: int, max_x: int, x1: int,
                 y1: int, dx: int, dy: int) -> List[int]:
    """ mtcnn results can be out of image so fix results
    """

    x2 = x1 + dx
    y2 = y1 + dy
    x1 = max(min(x1, max_x), 0)
    x2 = max(min(x2, max_x), 0)
    y1 = max(min(y1, max_y), 0)
    y2 = max(min(y2, max_y), 0)
    return [x1, y1, x2, y2]


def embedding_distance(embedding_1: Embedding,
                       embedding_2: Embedding,
                       distance_metric: DistanceMetric) -> float:
    """Compares the distance between two embeddings
    """
    distance = embedding_distance_bulk(embedding_1.reshape(
        1, -1), embedding_2.reshape(1, -1), distance_metric=distance_metric)[0]
    return distance


def embedding_distance_bulk(
        embeddings1: Embedding,
        embeddings2: Embedding,
        distance_metric: DistanceMetric) -> np.ndarray:
    """Compares the distance between two arrays of embeddings
    """
    if distance_metric == DistanceMetric.EUCLIDEAN_SQUARED:
        return np.square(
            paired_distances(
                embeddings1,
                embeddings2,
                metric='euclidean'))
    elif distance_metric == DistanceMetric.ANGULAR_DISTANCE:
        # Angular Distance: https://en.wikipedia.org/wiki/Cosine_similarity
        similarity = 1 - paired_distances(
            embeddings1,
            embeddings2,
            metric='cosine')
        return np.arccos(similarity) / math.pi


def download_image(url: str, is_rgb: bool=True) -> Image:
    req = urlopen(url)
    arr = np.asarray(bytearray(req.read()), dtype=np.uint8)
    # BGR color space
    image = cv2.imdecode(arr, -1)
    if is_rgb:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return image


def get_image_from_path_rgb(image_path: str) -> Image:
    # BGR color space
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return fix_image(image)


def get_image_from_path_bgr(image_path: str) -> Image:
    # BGR color space
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    return fix_image(image)


def get_images_from_dir(
        directory: str, recursive: bool, is_rgb: bool=True) -> ImageGenerator:
    if recursive:
        image_paths = iglob(os.path.join(
            directory, '**', '*.*'), recursive=recursive)
    else:
        image_paths = iglob(os.path.join(directory, '*.*'))
    for image_path in image_paths:
        # BGR color space
        image = cv2.imread(image_path)
        if is_rgb:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        yield image


def crop(image: Image, bb: List[int], margin: float) -> Image:
    """
    img = image from misc.imread, which should be in (H, W, C) format
    bb = pixel coordinates of bounding box: (x0, y0, x1, y1)
    margin = float from 0 to 1 for the amount of margin to add, relative to the
        bounding box dimensions (half margin added to each side)
    """

    if margin < 0:
        raise ValueError("the margin must be a value between 0 and 1")
    if margin > 1:
        raise ValueError(
            "the margin must be a value between 0 and 1 - this is a change from the existing API")

    img_height = image.shape[0]
    img_width = image.shape[1]
    x0, y0, x1, y1 = bb[:4]
    margin_height = (y1 - y0) * margin / 2
    margin_width = (x1 - x0) * margin / 2
    x0 = int(np.maximum(x0 - margin_width, 0))
    y0 = int(np.maximum(y0 - margin_height, 0))
    x1 = int(np.minimum(x1 + margin_width, img_width))
    y1 = int(np.minimum(y1 + margin_height, img_height))
    return image[y0:y1, x0:x1, :], (x0, y0, x1, y1)


def get_transform_matrix(left_eye: Tuple[int, int], right_eye: Tuple[int, int], desiredLeftEye: Tuple[float, float]=(
        0.35, 0.35), desiredFaceHeight: int=112, desiredFaceWidth: int=112, margin: float=0.0):
    # compute the angle between the eye centers
    dY = right_eye[1] - left_eye[1]
    dX = right_eye[0] - left_eye[0]
    angle = np.degrees(np.arctan2(dY, dX))

    # compute the desired right eye x-coordinate
    desiredRightEyeX = 1.0 - desiredLeftEye[0]

    # determine the scale of the new resulting image by taking
    dist = np.sqrt((dX ** 2) + (dY ** 2))
    desiredDist = (desiredRightEyeX - desiredLeftEye[0])
    desiredDist *= desiredFaceWidth
    scale = (desiredDist / dist)

    # median point between the two eyes in the input image
    x_center = (left_eye[0] + right_eye[0]) // 2
    y_center = (left_eye[1] + right_eye[1]) // 2
    eye_center = (x_center, y_center)

    # grab the rotation matrix for rotating and scaling the face
    M = cv2.getRotationMatrix2D(eye_center, angle, scale)

    # update the translation component of the matrix
    tX = (desiredFaceWidth * (margin + 1)) * 0.5
    tY = (desiredFaceHeight * (margin + 1)) * desiredLeftEye[1]
    x_shift = (tX - eye_center[0])
    y_shift = (tY - eye_center[1])
    M[0, 2] += x_shift
    M[1, 2] += y_shift
    return M


def preprocess(image: Image, desired_height: int, desired_width: int,
               margin: float, bbox: List[int]=None, landmark: Landmarks=None):
    image_height, image_width = image.shape[:2]
    margin_height = int(desired_height + desired_height * margin)
    margin_width = int(desired_width + desired_width * margin)
    M = None
    if landmark is not None:
        # Points chosen to align face in bottom center of image
        src = np.array([[38.2946, 51.6963],
                        [73.5318, 51.5014],
                        [56.0252, 71.7366],
                        [41.5493, 92.3655],
                        [70.7299, 92.2041]], dtype=np.float32)
        dx = (margin_width - 112) / 2
        dy = (margin_height - 112) / 2
        src[:, 0] += dx
        src[:, 1] += dy
        points = np.asarray([[point[0], point[1]]
                             for point in landmark.values()])
        dst = points.astype(np.float32)
        tform = trans.SimilarityTransform()
        tform.estimate(dst, src)
        M = tform.params[0:2, :]

    if bbox is None:
        # use center crop
        bbox = [0, 0, 0, 0]
        bbox[0] = int(image_height * 0.0625)
        bbox[1] = int(image_width * 0.0625)
        bbox[2] = image.shape[1] - bbox[0]
        bbox[3] = image.shape[0] - bbox[1]
    if M is None:
        cropped = crop(image, bbox, margin)[0]
        return cropped
    else:
        # do align using landmark
        warped = cv2.warpAffine(
            image, M, (margin_height, margin_width), flags=cv2.INTER_CUBIC)
        return warped