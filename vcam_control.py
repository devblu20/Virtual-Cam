import asyncio
import logging

import pyvirtualcam
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

logger = logging.getLogger("decart-live-camera.vcam")


class VcamError(RuntimeError):
    pass


_pc: RTCPeerConnection | None = None
_pump_task: asyncio.Task | None = None


def _probe_device() -> None:
    try:
        with pyvirtualcam.Camera(width=640, height=480, fps=20):
            pass
    except Exception as error:
        raise VcamError(
            "The virtual camera device is not available. If OBS is running, stop its Virtual Camera "
            "or close OBS, then try again. OBS Studio must be installed once so its camera driver exists."
        ) from error


async def start_session(offer_sdp: str, offer_type: str) -> dict[str, str]:
    global _pc
    await stop_session()
    _probe_device()

    pc = RTCPeerConnection()
    _pc = pc

    @pc.on("track")
    def on_track(track: MediaStreamTrack) -> None:
        global _pump_task
        if track.kind == "video" and _pump_task is None:
            _pump_task = asyncio.create_task(_pump_frames(track))

    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def _pump_frames(track: MediaStreamTrack) -> None:
    # The virtual-camera device is opened ONCE at a fixed size. Decart's output
    # resolution can change mid-stream, so every frame is resized to that fixed
    # size instead of reopening the device (reopening the OBS driver crashes and
    # leaves Meet stuck on the OBS logo placeholder).
    camera: pyvirtualcam.Camera | None = None
    out_w = 0
    out_h = 0
    try:
        while True:
            frame = await track.recv()
            if camera is None:
                out_w, out_h = frame.width, frame.height
                camera = pyvirtualcam.Camera(
                    width=out_w, height=out_h, fps=25, fmt=pyvirtualcam.PixelFormat.RGB
                )
                logger.info("Virtual camera live: %sx%s on %s", out_w, out_h, camera.device)
            if frame.width != out_w or frame.height != out_h:
                frame = frame.reformat(width=out_w, height=out_h, format="rgb24")
            image = frame.to_ndarray(format="rgb24")
            camera.send(image)
    except (MediaStreamError, asyncio.CancelledError):
        logger.info("Virtual camera stream ended.")
    except Exception:
        logger.exception("Virtual camera frame pump failed")
    finally:
        if camera is not None:
            camera.close()


async def stop_session() -> dict[str, bool]:
    global _pc, _pump_task
    if _pump_task is not None:
        _pump_task.cancel()
        try:
            await _pump_task
        except asyncio.CancelledError:
            pass
        _pump_task = None
    if _pc is not None:
        await _pc.close()
        _pc = None
    return {"stopped": True}
