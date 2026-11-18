"""Start the DRRCS desktop app."""
import argparse
import json
from pathlib import Path
from cloud_api import CloudAPI, ApiError


def main():
    parser = argparse.ArgumentParser(description="DRRCS Cloud Desktop")
    parser.add_argument("--config", default=str(Path(__file__).parent/".private/client.json"))
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text())
        api = CloudAPI(config)
        import tkinter as tk
        from app_ui import DesktopApp
        root = tk.Tk()
        DesktopApp(root, api)
        root.mainloop()
    except FileNotFoundError:
        print("Cloud connection file not found. See README.txt for setup.")
    except KeyboardInterrupt:
        print("\nApplication closed. Cloud records are retained.")
    except (OSError, json.JSONDecodeError, KeyError, ApiError) as error:
        print("Cannot start the cloud client: "+str(error))


if __name__ == "__main__":
    main()
