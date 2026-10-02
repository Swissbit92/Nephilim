"""Image generation: a supervised, detached subprocess.

Split deliberately into pieces that can be tested without an app:
``supervisor`` (spawn/probe/cancel), ``verify`` (is this really a PNG),
``jobdir`` (where it runs). The worker and lifespan sit on top.
"""
