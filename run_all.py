import subprocess
import sys
import logging

# --- Unified Launcher for BlueLink Bot ---

def run_services():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
    logging.info("🚀 Starting BlueBot Unified Services...")

    # Both the Telegram Bot and the Dashboard API Server are now 
    # integrated and run within the same process (lot2.py).
    # This ensures they share the same in-memory cache/state.
    
    try:
        logging.info("✅ Consolidated Services launching...")
        subprocess.run([sys.executable, "lot2.py"], check=True)
    except KeyboardInterrupt:
        logging.info("\n🛑 Shutting down BlueBot...")
    except Exception as e:
        logging.error(f"❌ Critical error in main launcher: {e}")

if __name__ == "__main__":
    run_services()

