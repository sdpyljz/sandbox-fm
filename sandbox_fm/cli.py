# -*- coding: utf-8 -*-

import pathlib
import logging
import time
import json
import functools
import itertools

import skimage.io
import scipy.interpolate
import cv2
import tqdm
import click
import numpy as np
import matplotlib.path
import matplotlib.backend_bases
import matplotlib.pyplot as plt

import mpi4py.MPI
import bmi.wrapper

from .depth import (
    depth_images,
    calibrated_height_images,
    video_images
)
from .calibrate import (
    compute_affines,
    transform
)
from .plots import Visualization
from .sandbox_fm import (
    update_delft3d_initial_vars,
    update_delft3d_vars,
    compute_delta_zk,
    compute_delta_s1
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@click.group()
def cli():
    pass

@cli.command()
def record():
    """record 10 frames, for testing"""
    videos = video_images()
    depths = depth_images()
    raws = depth_images(raw=True)
    for i, (video, depth, raw) in enumerate(zip(videos, depths, raws)):
        skimage.io.imsave("video_%06d.png" % (i, ), video)
        skimage.io.imsave("depth_%06d.png" % (i, ), depth)
        raw.dump("raw_%06d.npy" % (i, ))
        if i > 5:
            break



@cli.command()
@click.argument('schematization', type=click.File('rb'))
def calibrate(schematization):
    """calibrate the sandbox by selecting both 4 points in box and in model"""

    # raw images
    videos = video_images()
    depths = depth_images()
    raws = depth_images(raw=True)
    # get video and depth image
    video = next(videos)
    depth = next(depths)
    raw = next(raws)

    # save the current working directory
    curdir = pathlib.Path.cwd()

    fig, axes = plt.subplots(2, 2)
    # sic show instructions in the title
    fig.suptitle('select 4 points (clockwise start at top left)')

    # show the depth and video in the left window
    axes[0, 0].imshow(video)
    axes[0, 0].imshow(depth, alpha=0.3, cmap='Reds')
    axes[1, 0].imshow(raw)

    # keep track of the selected points
    img_points = []
    model_points = []
    height_points = []
    z_values = []

    # define fixed box coordinate system (what will be on the screen)
    box = np.array([
        [0, 0],
        [640, 0],
        [640, 480],
        [0, 480]
    ], dtype='float32')

    # pointer event
    pid = None

    # define the point selector
    def picker(event):
        if (event.inaxes == axes[0, 0] and len(img_points) < 4):
            img_points.append((event.xdata, event.ydata))
            event.inaxes.set_title('%s points selected' % (
                len(img_points), )
            )
        elif (event.inaxes == axes[0, 1] and len(model_points) < 4):
            model_points.append((event.xdata, event.ydata))
            event.inaxes.set_title('%s points selected' % (
                len(model_points), )
            )
        elif (event.inaxes == axes[1, 0] and len(height_points) < 2):
            height_points.append((event.xdata, event.ydata))
            z_values.append(float(raw[int(event.ydata), int(event.xdata)]))
            title = "%s points selected" % (len(height_points), )
            if (len(height_points) == 0):
                title = "select a point at -8m"
            elif (len(height_points) == 1):
                title = "select a point at 12m"
            event.inaxes.set_title(title)
            event.inaxes.plot(event.xdata, event.ydata, 'ko')
            event.inaxes.text(
                event.xdata + 0.5,
                event.ydata + 0.5,
                "d: %.2f\n(%s, %s)" % (raw[int(event.ydata), int(event.xdata)], int(event.xdata), int(event.ydata))
            )
        if len(img_points) == 4 and len(model_points) == 4 and len(height_points) == 2:
            # stop listening we're done
            fig.canvas.mpl_disconnect(pid)
            save_and_show_result(axes[1, 1])

    def save_and_show_result(ax):
        # we should have results by now
        model2box = cv2.getPerspectiveTransform(
            np.array(model_points, dtype='float32'),
            box
        )
        print(img_points, box)
        img2box = cv2.getPerspectiveTransform(
            np.array(img_points, dtype='float32'),
            box
        )
        img2model = cv2.getPerspectiveTransform(
            np.array(img_points, dtype='float32'),
            np.array(model_points, dtype='float32')
        )
        model2img = cv2.getPerspectiveTransform(
            np.array(model_points, dtype='float32'),
            np.array(img_points, dtype='float32')
        )
        box2model = cv2.getPerspectiveTransform(
            np.array(box, dtype='float32'),
            np.array(model_points, dtype='float32')
        )
        box2img = cv2.getPerspectiveTransform(
            np.array(box, dtype='float32'),
            np.array(img_points, dtype='float32')
        )

        comment = """
        This file contains calibrations for model %s.
        It is generated with the perspective transform from opencv.
        """ % (schematization_path, )
        result = {
            "model2box": model2box.tolist(),
            "img2box": img2box.tolist(),
            "img2model": img2model.tolist(),
            "model2img": model2img.tolist(),
            "box2model": box2model.tolist(),
            "box2img": box2img.tolist(),
            "img_points": img_points,
            "model_points": model_points,
            "height_points": height_points,
            "z_values": z_values,
            "z": [-8, 12],
            "_comment": comment

        }
        # save the calibration info
        with open(str(curdir / 'calibration.json'), 'w') as f:
            json.dump(result, f, indent=2)


        # now for showing results
        xy_nodes_in_img = np.squeeze(
            cv2.perspectiveTransform(
                np.dstack([
                    xy_node[:, np.newaxis, 0],
                    xy_node[:, np.newaxis, 1]
                ]).astype('float32'),
                model2box
            )
        )
        # scatter plot
        ax.scatter(
            xy_nodes_in_img[:, 0],
            xy_nodes_in_img[:, 1],
            c=data['zk'].ravel(),
            cmap='Greens',
            edgecolor='none',
            s=20,
            alpha=0.5
        )
        # transformed video on top
        ax.imshow(
            cv2.warpPerspective(
                video,
                img2box,
                (640, 480)
            ),
            cmap='Reds',
            alpha=0.5
        )
        ax.set_title('You are done (result below)')
        plt.show()

    # start the model (changes directory)
    model = bmi.wrapper.BMIWrapper('dflowfm')
    schematization_path = pathlib.Path(schematization.name)
    model.initialize(str(schematization_path.absolute()))
    data = {}
    update_delft3d_initial_vars(data, model)
    # convert to array we can feed into opencv
    xy_node = np.c_[
        data['xk'],
        data['yk'],
        np.ones_like(data['xk'])
    ].astype('float32')


    axes[0, 1].scatter(data['xk'].ravel(), data['yk'].ravel(), c=data['zk'].ravel(), cmap='Greens', edgecolor='none')
    plt.ion()
    pid = fig.canvas.mpl_connect('button_press_event', picker)
    plt.show(block=True)


@cli.command()
def view():
    """view raw kinect images"""
    with open("calibration.json") as f:
        calibration = json.load(f)
    images = calibrated_height_images(calibration["z_values"], calibration["z"])
    origin = 'bottom'

    fig, ax = plt.subplots(frameon=False)
    manager = plt.get_current_fig_manager()
    try:
        manager.resize(*manager.window.maxsize())
    except AttributeError:
        # no resize available
        pass
    fig.tight_layout()
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    ax.set_axis_off()
    ax.set_frame_on(False)
    im = ax.imshow(next(images), origin='upper', cmap='terrain')
    plt.ion()
    plt.show(block=False)
    for img in tqdm.tqdm(images):
        im.set_data(img)
        fig.canvas.draw()




@cli.command()
@click.argument('schematization', type=click.File('rb'))
def run(schematization):
    """Console script for sandbox_fm"""
    click.echo("Make sure you start the SARndbox first")

    # calibration info
    data = {}
    with open('calibration.json') as f:
        calibration = json.load(f)
    data.update(calibration)
    with open('config.json') as f:
        configuration = json.load(f)
    data.update(configuration)

    # model
    model = bmi.wrapper.BMIWrapper('dflowfm')
    # initialize model schematization, changes directory
    model.initialize(str(pathlib.Path(schematization.name).absolute()))
    update_delft3d_initial_vars(data, model)
    dt = model.get_time_step()

    model_bbox = matplotlib.path.Path(data['model_points'])
    data['node_in_box'] = model_bbox.contains_points(np.c_[data['xk'], data['yk']])
    data['cell_in_box'] = model_bbox.contains_points(np.c_[data['xzw'], data['yzw']])
    
    img_bbox = matplotlib.path.Path([
        (40, 40),
        (40, 480),
        (600, 480),
        (600, 40)
    ])
    xzw_box, yzw_box = transform(data['xzw'], data['yzw'], data['model2box'])
    xk_box, yk_box = transform(data['xk'], data['yk'], data['model2box'])
    print(xzw_box.min(), xzw_box.max())
    data['cell_in_img_bbox'] = img_bbox.contains_points(np.c_[xzw_box, yzw_box])
    data['node_in_img_bbox'] = img_bbox.contains_points(np.c_[xk_box, yk_box])
    if data.get('debug'):
        plt.scatter(data['xzw'], data['yzw'], c=data['cell_in_img_bbox'], edgecolor='none')
        plt.show()
        plt.scatter(data['xzw'], data['yzw'], c=data['cell_in_box'], edgecolor='none')
        plt.show()
    # images
    heights = calibrated_height_images(calibration["z_values"], calibration["z"])
    videos = video_images()
    # load model library
    height = next(heights)
    video = next(videos)

    data['height'] = height.copy()
    data['height'] = height
    data['video'] = video


    vis = Visualization()
    update_delft3d_vars(data, model)
    vis.initialize(data)


    def callback(evt, data, model, vis=vis):
        if not isinstance(evt, matplotlib.backend_bases.KeyEvent):
            return
        if evt.key == 'b':
            # data['bl'][idx] += compute_delta_bl(data, idx)
            idx = data['node_in_box']
            zk_copy = data['zk'].copy()
            zk_copy[idx] += compute_delta_zk(data, idx)
            # replace the part that changed
            for i in np.where(idx)[0]:
                if data['zk'][i] != zk_copy[i]:
                    # TODO: bug in zk
                    model.set_var_slice('zk', [i+1], [1], zk_copy[i:i+1])
        if evt.key == 'c':
            if not vis.im_flow.get_visible():
                vis.lic[:, :, :3] = 1.0
                vis.lic[:, :, 3] = 0.0
                print(data['video'].dtype)
                vis.lic = cv2.warpPerspective(
                    data['video'].astype('float32')/255.0,
                    np.array(data['img2box']),
                    data['height'].shape[::-1]
                )
                if vis.lic.shape[-1] == 3:
                    # add depth channel
                    vis.lic = np.dstack([vis.lic, np.ones_like(vis.lic[:, :, 0])])
                
            vis.im_flow.set_visible(not vis.im_flow.get_visible())


    vis.subscribers.append(
        # fill in the data parameter and subscribe to events
        functools.partial(callback, data=data, model=model, vis=vis)
    )

    for i in range(10):
        model.update(dt)


    for i, (video, height) in enumerate(tqdm.tqdm(itertools.izip(videos, heights))):
        update_delft3d_vars(data, model)
        data['height'] = height
        data['video'] = video

        # only change bathymetry of wet cells
        idx = np.logical_and(data['cell_in_box'], data['is_wet']) #

        # if vis.im_flow.get_visible():
        #     # idx = data['cell_in_box']
        #     delta_s1 = compute_delta_s1(data, idx)
        #     print(delta_s1.max(), delta_s1.min())
        #     data['s1'][idx] += delta_s1

        vis.update(data)
        tic = time.time()
        model.update(dt)
        toc = time.time()
        print(toc - tic)

if __name__ == "__main__":
    main()
