import json
import os
from pathlib import Path
from typing import Any

import obsws_python as obs


SCENE_NAME = "Decart Live Camera"
SOURCE_NAME = "Decart Browser Output"


class OBSControlError(RuntimeError):
    pass


def _config_path() -> Path:
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise OBSControlError("Windows APPDATA is unavailable.")
    return Path(appdata) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"


def _connection_settings() -> tuple[str, int, str]:
    path = _config_path()
    if not path.is_file():
        raise OBSControlError("OBS WebSocket configuration was not found. Install OBS Studio 28 or newer.")

    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise OBSControlError("OBS WebSocket configuration could not be read.") from error

    if not config.get("server_enabled", False):
        raise OBSControlError(
            "One-time step required: in OBS open Tools → WebSocket Server Settings, enable the server, "
            "click Apply, and restart OBS. Keep authentication enabled."
        )

    return "127.0.0.1", int(config.get("server_port", 4455)), str(config.get("server_password", ""))


def _connect() -> obs.ReqClient:
    host, port, password = _connection_settings()
    try:
        return obs.ReqClient(host=host, port=port, password=password, timeout=5)
    except Exception as error:
        raise OBSControlError("OBS is not reachable. Open OBS Studio and try again.") from error


def get_status() -> dict[str, Any]:
    try:
        client = _connect()
        try:
            version = client.get_version()
            virtual_camera = client.get_virtual_cam_status()
            return {
                "available": True,
                "connected": True,
                "version": version.obs_version,
                "virtualCameraActive": virtual_camera.output_active,
            }
        finally:
            client.disconnect()
    except OBSControlError as error:
        return {"available": False, "connected": False, "message": str(error)}


def _find_decart_window(client: obs.ReqClient) -> str:
    items = client.get_input_properties_list_property_items(SOURCE_NAME, "window").property_items
    candidates = [item for item in items if item.get("itemEnabled", True)]

    for item in candidates:
        label = f"{item.get('itemName', '')} {item.get('itemValue', '')}".lower()
        if "decart live camera" in label and ("chrome" in label or "msedge" in label or "edge" in label):
            return str(item["itemValue"])

    for item in candidates:
        label = f"{item.get('itemName', '')} {item.get('itemValue', '')}".lower()
        if "decart live camera" in label:
            return str(item["itemValue"])

    raise OBSControlError(
        "The Decart browser window was not found. Keep http://localhost:5173 open in Chrome or Edge and try again."
    )


def start_zoom_camera() -> dict[str, Any]:
    client = _connect()
    try:
        scenes = client.get_scene_list().scenes
        if not any(scene.get("sceneName") == SCENE_NAME for scene in scenes):
            client.create_scene(SCENE_NAME)

        inputs = client.get_input_list().inputs
        if not any(item.get("inputName") == SOURCE_NAME for item in inputs):
            client.create_input(SCENE_NAME, SOURCE_NAME, "window_capture", {}, True)
        else:
            scene_items = client.get_scene_item_list(SCENE_NAME).scene_items
            if not any(item.get("sourceName") == SOURCE_NAME for item in scene_items):
                client.create_scene_item(SCENE_NAME, SOURCE_NAME, True)

        window_value = _find_decart_window(client)
        client.set_input_settings(
            SOURCE_NAME,
            {"window": window_value, "cursor": False, "client_area": True},
            True,
        )

        item_id = client.get_scene_item_id(SCENE_NAME, SOURCE_NAME).scene_item_id
        video = client.get_video_settings()
        client.set_scene_item_transform(
            SCENE_NAME,
            item_id,
            {
                "positionX": 0,
                "positionY": 0,
                "boundsType": "OBS_BOUNDS_SCALE_INNER",
                "boundsWidth": video.base_width,
                "boundsHeight": video.base_height,
                "boundsAlignment": 0,
            },
        )
        client.set_current_program_scene(SCENE_NAME)

        status = client.get_virtual_cam_status()
        if not status.output_active:
            client.start_virtual_cam()

        return {
            "started": True,
            "scene": SCENE_NAME,
            "message": "OBS Virtual Camera is running. Select it once in Zoom's Camera menu.",
        }
    except OBSControlError:
        raise
    except Exception as error:
        raise OBSControlError("OBS could not prepare the Decart scene. Check the OBS log and try again.") from error
    finally:
        client.disconnect()


def stop_zoom_camera() -> dict[str, bool]:
    client = _connect()
    try:
        status = client.get_virtual_cam_status()
        if status.output_active:
            client.stop_virtual_cam()
        return {"stopped": True}
    except Exception as error:
        raise OBSControlError("OBS Virtual Camera could not be stopped.") from error
    finally:
        client.disconnect()
