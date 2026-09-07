"""The first execution profile is pinned to source, never inferred from shapes."""
from .contracts import Manifest

OPENPILOT_REVISION = "555f48c5d28709f039b79f3f6105e51305edd4b5"
TINYGRAD_REVISION = "138fb4a783d82f4e877ad2fe3692aaf8d1de2e46"
STOCK_SHA256 = "659727c4d4839adc4992a254409a54259a8756a743f2d567bf5fdc6579f8009b"
STOCK_INPUT_SHAPES = {"img": [1, 12, 128, 256], "big_img": [1, 12, 128, 256],
                      "features_buffer": [1, 24, 512], "desire_pulse": [1, 25, 8],
                      "traffic_convention": [1, 2], "action_t": [1, 2]}
STOCK_SLICES = {"meta": [0, 55, None], "desire_pred": [55, 87, None], "pose": [87, 99, None],
                "wide_from_device_euler": [99, 105, None], "road_transform": [105, 117, None],
                "lane_lines": [117, 645, None], "lane_lines_prob": [645, 653, None],
                "road_edges": [653, 917, None], "lead": [917, 1061, None],
                "lead_prob": [1061, 1064, None], "hidden_state": [1064, 1576, None],
                "plan": [1576, 2566, None], "desire_state": [2566, 2574, None], "pad": [2574, 2576, None]}
STOCK_PROFILE = Manifest.create({
  "schema": 1, "type": "profile", "name": "comma/driving-supercombo-555f48c5/v1",
  "slot_sets": [["supercombo"]], "connections": [],
  "required_configuration": ["frame_skip", "LAT_SMOOTH_SECONDS", "LONG_SMOOTH_SECONDS"],
  "inputs": {
    "frames": {"names": ["road", "wide"], "format": "NV12", "width": 1928, "height": 1208,
               "stride": 2048, "uv_offset": 2490368, "allocation_bytes": 4804608,
               "ownership": "borrowed until synchronous step returns"},
    "transforms": {"shape": [2, 3, 3], "meaning": "model-to-camera inverse homographies; road then wide"},
    "desire": {"shape": [8], "meaning": "upstream desire encoding; rising edges; index 0 ignored"},
    "traffic_convention": {"shape": [1, 2], "meaning": "left-hand then right-hand driving one-hot"},
    "action_t": {"shape": [1, 2], "units": "seconds", "order": ["lateral", "longitudinal"]},
    "timing": {"clock": "consumer monotonic nanoseconds", "cadence_hz": 20, "max_camera_skew_ns": 10000000}},
  "outputs": {"raw": {"shape": [2576], "dtype": "float32", "slices": STOCK_SLICES},
              "decoded": "pinned Parser arrays; plan XYZ position/metres, velocity/metres per second, acceleration/metres per second squared, Euler/radians, angular rate/radians per second",
              "time_indices_seconds": [10 * (i / 32) ** 2 for i in range(33)],
              "publication": "consumer-owned; no cereal messages or actuation"},
  "state": {"frame_skip": 4, "context_hz": 5, "initialization": "zero queues and recurrent state",
            "warmup": "execute a dummy inference then reset", "first_result": "fifth consecutive frame pair",
            "discontinuity": "reject and reset on missing/reordered frames or cadence gap outside 25-75ms",
            "skipped_evaluation": "unsupported; skipping step requires reset", "failure": "reset; no cached output"},
  "implementation_source": {"repository": "commaai/openpilot", "commit": OPENPILOT_REVISION,
                            "tinygrad": TINYGRAD_REVISION},
})
