"""Local launcher: python run_app.py  (then open http://127.0.0.1:5000)"""
import os
from app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
