__version__ = "0.0.1"


def _apply_startup_overrides():
	"""Runs once per process (web worker, background worker, or scheduler) at
	app import time -- see klik_pos/overrides/etims_walkin_pin.py for why it
	has to run this early rather than off a web-request hook. Must stay
	side-effect-free on failure: this executes during Frappe's app bootstrap,
	sometimes outside a fully connected site context (e.g. `bench build`), so
	it can never assume a working database connection.
	"""
	try:
		from klik_pos.overrides.etims_walkin_pin import apply_walkin_pin_override

		apply_walkin_pin_override()
	except Exception as e:  # pragma: no cover - defensive, must never break app boot
		print(f"[klik_pos] Startup override skipped: {e}")


_apply_startup_overrides()