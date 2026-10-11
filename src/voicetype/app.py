"""App wiring: config -> SettingsStore -> pipeline -> tray + main window."""
from __future__ import annotations

import argparse
import logging

from voicetype.config_manager import ConfigManager
from voicetype.pipeline import VoicePipeline
from voicetype.settings_store import SettingsStore


def create_app(user_config=None, overrides=None):
    mgr = ConfigManager(user_path=user_config, overrides=overrides)
    logging.basicConfig(level=getattr(logging, str(mgr.get("logging.level", "INFO")).upper(),
                                      logging.INFO))
    pipeline = VoicePipeline(mgr.config)
    store = SettingsStore(mgr, pipeline)
    return pipeline, mgr.config, store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="voicetype", description="Voice-first keyboard replacement")
    ap.add_argument("--config", default=None, help="Path to user config YAML")
    ap.add_argument("--no-tray", action="store_true", help="Run headless (no system tray)")
    ap.add_argument("--check", action="store_true", help="Print resolved config and exit")
    ap.add_argument("--list-mics", action="store_true",
                    help="List OS microphone input devices and exit")
    ap.add_argument("--companion", action="store_true",
                    help="Start the LAN companion server for the mobile app")
    ap.add_argument("--show-qr", action="store_true",
                    help="Print a fresh pairing URI (and QR if available) and exit")
    args = ap.parse_args(argv)
    if args.list_mics:
        from voicetype.capture import AudioCapture
        for i, dev in enumerate(AudioCapture.list_input_devices()):
            print(f"[{i}] {dev.get('name')} "
                  f"(in={dev.get('max_input_channels')}, default_sr={dev.get('default_samplerate')})")
        return 0
    pipeline, config, store = create_app(args.config)
    if args.check:
        import yaml
        print(yaml.safe_dump(config, sort_keys=False))
        return 0
    companion_handles = None
    if args.show_qr or args.companion or config.get("companion", {}).get("enabled"):
        from voicetype.companion.server import start_companion_services
        companion_handles = start_companion_services(config, pipeline)
        payload = companion_handles["payload"]
        if payload is not None:
            print(f"Pairing URI: {payload.to_uri()}")
            print(f"Cert fingerprint (verify on phone): {payload.fingerprint}")
            if companion_handles["qr_png"]:
                out = "voicetype-pairing-qr.png"
                with open(out, "wb") as fh:
                    fh.write(companion_handles["qr_png"])
                print(f"QR code written to {out}")
            else:
                print("(pip install qrcode for a scannable PNG; URI above works for manual entry)")
        if companion_handles["tls_error"]:
            print(companion_handles["tls_error"])
        adv = companion_handles["advertiser"]
        print(f"mDNS: {'advertised' if adv.ok else adv.reason}")
        if args.show_qr:
            return 0
        companion_handles["server"].serve_in_background()
    if args.no_tray:
        pipeline.start()
        print("VoiceType running headless. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            pipeline.stop()
        return 0
    from voicetype.tray import TrayApp
    TrayApp(pipeline, config, store).run()
    return 0
