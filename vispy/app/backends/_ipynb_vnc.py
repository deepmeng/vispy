# -*- coding: utf-8 -*-
# Copyright (c) 2014, Vispy Development Team.
# Distributed under the (new) BSD License. See LICENSE.txt for more info.

"""
vispy backend for the IPython notebook (vnc approach).

We aim to have:
* ipynb_static - export visualization to a static notebook
* ipynb_vnc - vnc-approach: render in Python, send result to JS as png
* ipynb_webgl - send gl commands to JS and execute in webgl context

"""

from __future__ import division

from ..base import (BaseApplicationBackend, BaseCanvasBackend,
                    BaseTimerBackend)
from .. import Application, Canvas
from ...util import logger

# Imports for screenshot
# Perhaps we should refactor these to have just one import
from ...gloo.util import _screenshot
from ...util import make_png
from base64 import b64encode

# Import for displaying Javascript on notebook
import os.path as op

# -------------------------------------------------------------------- init ---

capability = dict(  # things that can be set by the backend
    title=True,  # But it only applies to the dummy window :P
    size=True,  # We cannot possibly say we dont, because Canvas always sets it
    position=True,  # Dito
    show=True,  # Note: we don't alow this, but all scripts call show ...
    vsync=False,
    resizable=True,  # Yes, you can set to not be resizable (it always is)
    decorate=False,
    fullscreen=False,
    context=True,
    multi_window=True,
    scroll=True,
    parent=False,
)


def _set_config(c):
    _app.backend_module._set_config(c)


# Create our "backend" backend; The toolkit that is going to provide a
# canvas (e.g. OpenGL context) so we can render images.
# Note that if IPython has already loaded a GUI backend, vispy is
# probably going to use that as well, because it prefers loaded backends.
try:
    # Explicitly use default (avoid using test-app)
    _app = Application('default')
except RuntimeError:
    _msg = 'ipynb_vnc backend relies on a core backend'
    available, testable, why_not, which = False, False, _msg, None
else:
    # Try importing IPython
    try:
        from IPython.html.widgets import DOMWidget
        from IPython.utils.traitlets import Unicode, Int, Float
        from IPython.display import display, Javascript
        from IPython.html.nbextensions import install_nbextension
    except Exception as exp:
        available, testable, why_not = False, False, str(exp)
    else:
        # Check if not GLUT, because that is going to be too unstable
        if 'glut' in _app.backend_module.__name__:
            _msg = 'ipynb_vnc backend refuses to work with GLUT'
            available, testable, why_not = False, False, _msg
        else:
            available, testable, why_not = True, True, None
        which = _app.backend_module.which
    # Use that backend's shared context
    KEYMAP = _app.backend_module.KEYMAP
    SharedContext = _app.backend_module.SharedContext


# ------------------------------------------------------------- application ---

# todo: maybe trigger something in JS on any of these methods?
class ApplicationBackend(BaseApplicationBackend):

    def __init__(self):
        BaseApplicationBackend.__init__(self)
        self._backend2 = _app._backend

    def _vispy_get_backend_name(self):
        realname = self._backend2._vispy_get_backend_name()
        return 'ipynb_vnc (via %s)' % realname

    def _vispy_process_events(self):
        return self._backend2._vispy_process_events()

    def _vispy_run(self):
        return self._backend2._vispy_run()

    def _vispy_quit(self):
        return self._backend2._vispy_quit()

    def _vispy_get_native_app(self):
        return self._backend2._vispy_get_native_app()


# ------------------------------------------------------------------ canvas ---

class CanvasBackend(BaseCanvasBackend):

    def __init__(self, *args, **kwargs):
        BaseCanvasBackend.__init__(self, capability, SharedContext)

        # Test kwargs
#         if kwargs['size']:
#             raise RuntimeError('ipynb_vnc Canvas is not resizable')
#         if kwargs['position']:
#             raise RuntimeError('ipynb_vnc Canvas is not positionable')
        if not kwargs['decorate']:
            raise RuntimeError('ipynb_vnc Canvas is not decoratable (or not)')
        if kwargs['vsync']:
            raise RuntimeError('ipynb_vnc Canvas does not support vsync')
        if kwargs['fullscreen']:
            raise RuntimeError('ipynb_vnc Canvas does not support fullscreen')

        # Create real canvas. It is a backend to this backend
        kwargs.pop('vispy_canvas', None)
        kwargs['autoswap'] = False
        canvas = Canvas(app=_app, **kwargs)
        self._backend2 = canvas.native

        # Connect to events of canvas to keep up to date with size and draws
        canvas.events.draw.connect(self._on_draw)
        canvas.events.resize.connect(self._on_resize)
        self._initialized = False

        # Show the widget
        canvas.show()
        # todo: hide that canvas

        # Prepare Javascript code by displaying on notebook
        self._prepare_js()
        # Create IPython Widget
        self._widget = Widget(self._gen_event, size=canvas.size)

    @property
    def _vispy_context(self):
        """Context to return for sharing"""
        return self._backend2._vispy_context

    def _vispy_warmup(self):
        return self._backend2._vispy_warmup()

    def _vispy_set_current(self):
        return self._backend2._vispy_set_current()

    def _vispy_swap_buffers(self):
        return self._backend2._vispy_swap_buffers()

    def _vispy_set_title(self, title):
        return self._backend2._vispy_set_title(title)
        #logger.warning('IPython notebook canvas has not title.')

    def _vispy_set_size(self, w, h):
        self._widget.size = w, h
        return self._backend2._vispy_set_size(w, h)

    def _vispy_set_position(self, x, y):
        logger.warning('IPython notebook canvas cannot be repositioned.')

    def _vispy_set_visible(self, visible):
        #self._backend2._vispy_set_visible(visible)
        if not visible:
            logger.warning('IPython notebook canvas cannot be hidden.')
        else:
            display(self._widget)

    def _vispy_update(self):
        return self._backend2._vispy_update()

    def _vispy_close(self):
        self._widget.close()
        return self._backend2._vispy_close()

    def _vispy_get_position(self):
        return 0, 0

    def _vispy_get_size(self):
        return self._backend2._vispy_get_size()

    def _on_resize(self, event=None):
        # Event handler that is called by the underlying canvas
        if self._vispy_canvas is None:
            return
        size = self._backend2._vispy_get_size()
        self._vispy_canvas.events.resize(size=size)

    def _on_draw(self, event=None):
        # Event handler that is called by the underlying canvas
        if self._vispy_canvas is None:
            return
        # Handle initialization
        if not self._initialized:
            self._initialized = True
            self._vispy_canvas.events.initialize()
            self._on_resize()
        # Normal behavior
        self._vispy_set_current()
        self._vispy_canvas.events.draw(region=None)
        # Save the encoded screenshot image to widget
        self._save_screenshot()

    def _save_screenshot(self):
        # Take the screenshot
        img = _screenshot()
        # Convert to PNG and encode
        self._widget.value = b64encode(make_png(img))

    # Generate vispy events according to upcoming JS events
    def _gen_event(self, ev):
        if self._vispy_canvas is None:
            return

        ev = ev.get("event")
        # Parse and generate event
        if ev.get("name") == "MouseEvent":
            mouse = ev.get("properties")
            # Generate
            if mouse.get("type") == "mouse_move":
                self._vispy_mouse_move(native=mouse,
                                       pos=mouse.get("pos"),
                                       modifiers=mouse.get("modifiers"),
                                       )
            elif mouse.get("type") == "mouse_press":
                self._vispy_mouse_press(native=mouse,
                                        pos=mouse.get("pos"),
                                        button=mouse.get("button"),
                                        modifiers=mouse.get("modifiers"),
                                        )
            elif mouse.get("type") == "mouse_release":
                self._vispy_mouse_release(native=mouse,
                                          pos=mouse.get("pos"),
                                          button=mouse.get("button"),
                                          modifiers=mouse.get("modifiers"),
                                          )
            elif mouse.get("type") == "mouse_wheel":
                self._vispy_canvas.events.mouse_wheel(native=mouse,
                                                      delta=mouse.get("delta"),
                                                      pos=mouse.get("pos"),
                                                      modifiers=mouse.get
                                                      ("modifiers"),
                                                      )
        elif ev.get("name") == "KeyEvent":
            key = ev.get("properties")
            if key.get("type") == "key_press":
                self._vispy_canvas.events.key_press(native=key,
                                                    key=key.get("key"),
                                                    modifiers=key.get
                                                    ("modifiers"),
                                                    )
            elif key.get("type") == "key_release":
                self._vispy_canvas.events.key_release(native=key,
                                                      key=key.get("key"),
                                                      modifiers=key.get
                                                      ("modifiers"),
                                                      )
        elif ev.get("name") == "TimerEvent":  # Ticking from front-end (JS)
            self._vispy_canvas.events.timer(type="timer")

    def _prepare_js(self):
        pkgdir = op.dirname(__file__)
        install_nbextension([op.join(pkgdir, '../../html/static/js')])
        script = 'IPython.load_extensions("js/vispy");'
        display(Javascript(script))


# ------------------------------------------------------------------- timer ---

class TimerBackend(BaseTimerBackend):

    def __init__(self, vispy_timer):
        self._backend2 = _app.backend_module.TimerBackend(vispy_timer)

    def _vispy_start(self, interval):
        return self._backend2._vispy_start(interval)

    def _vispy_stop(self):
        return self._backend2._vispy_stop()

    def _vispy_timeout(self):
        return self._backend2._vispy_timeout()


# ---------------------------------------------------------- IPython Widget ---

class Widget(DOMWidget):
    _view_name = Unicode("Widget", sync=True)

    # Define the custom state properties to sync with the front-end
    format = Unicode('png', sync=True)
    width = Int(sync=True)
    height = Int(sync=True)
    interval = Float(sync=True)
    value = Unicode(sync=True)

    def __init__(self, gen_event, **kwargs):
        super(Widget, self).__init__(**kwargs)
        self.size = kwargs["size"]
        self.interval = 100
        self.gen_event = gen_event
        self.on_msg(self._handle_event_msg)

    def _handle_event_msg(self, _, content):
        self.gen_event(content)

    @property
    def size(self):
        return self.width, self.height

    @size.setter
    def size(self, size):
        self.width, self.height = size
