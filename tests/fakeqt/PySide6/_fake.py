"""A permissive, *stateful enough* stand-in for PySide6 used only to smoke-test
UI wiring in environments where PySide6 cannot be installed.  Real signals,
sliders, spin boxes, check buttons, combo boxes and tree widgets are
emulated; everything else is a harmless no-op returning a numeric-zero dummy.
"""
import inspect
import itertools

_bits = itertools.count(1)
_flag_cache = {}


class Dummy:
    """Callable, attribute-able, behaves as 0 numerically."""
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return Dummy()
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Dummy()
    def __bool__(self): return False
    def __int__(self): return 0
    def __float__(self): return 0.0
    def __index__(self): return 0
    def __floor__(self): return 0
    def __round__(self, n=None): return 0
    def __iter__(self): return iter(())
    def __len__(self): return 0
    def __eq__(self, o): return isinstance(o, Dummy) or o == 0
    def __hash__(self): return 0
    def __lt__(self, o): return 0 < o
    def __le__(self, o): return 0 <= o
    def __gt__(self, o): return 0 > o
    def __ge__(self, o): return 0 >= o
    def _n(self, o): return 0
    __add__ = __radd__ = __sub__ = __mul__ = __rmul__ = __truediv__ = __floordiv__ = __mod__ = lambda s, o: 0
    def __rsub__(self, o): return o
    def __rtruediv__(self, o): return 0
    __or__ = __ror__ = __and__ = __rand__ = lambda s, o: 0
    def __neg__(self): return 0
    def __invert__(self): return 0
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Flag(int):
    def __call__(self, *a, **k): return Dummy()
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _flag(n)


def _flag(name):
    if name not in _flag_cache:
        _flag_cache[name] = Flag(1 << next(_bits))
    return _flag_cache[name]


def _arity(fn):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return 99
    n = 0
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL,):
            return 99
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            n += 1
    return n


class BoundSignal:
    def __init__(self, owner=None):
        self.slots = []
        self.owner = owner
    def connect(self, fn, *a):
        self.slots.append(fn)
    def disconnect(self, *a):
        self.slots.clear()
    def emit(self, *args):
        if self.owner is not None and getattr(self.owner, "_blocked", False):
            return
        for fn in list(self.slots):
            if isinstance(fn, BoundSignal):
                fn.emit(*args)
                continue
            n = _arity(fn)
            fn(*args[:n])
    __call__ = emit


class Signal:
    def __init__(self, *types, **k):
        self.name = None
    def __set_name__(self, owner, name):
        self.name = name
    def __get__(self, inst, owner):
        if inst is None:
            return self
        key = "_sig_" + self.name
        d = inst.__dict__
        if key not in d:
            d[key] = BoundSignal(inst)
        return d[key]


class _Meta(type):
    def __getattr__(cls, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _flag(n)


class Base(metaclass=_Meta):
    """Generic QObject/QWidget stand-in."""
    def __init__(self, *a, **k):
        self._blocked = False
        self._visible = True
        self._enabled = True
        self._props = {}
        self._w, self._h = 800, 600
    def __getattr__(self, n):
        if n.startswith("__") or n.startswith("_"):
            raise AttributeError(n)
        if n[:1].islower() and n in _SIGNAL_NAMES:
            sig = BoundSignal(self)
            self.__dict__[n] = sig
            return sig
        return Dummy()
    # common real behaviour
    def blockSignals(self, b):
        old = self._blocked
        self._blocked = b
        return old
    def signalsBlocked(self): return self._blocked
    def setVisible(self, v): self._visible = v
    def isVisible(self): return self._visible
    def show(self): self._visible = True
    def hide(self): self._visible = False
    def setEnabled(self, v): self._enabled = bool(v)
    def isEnabled(self): return self._enabled
    def width(self): return self._w
    def height(self): return self._h
    def resize(self, w, h=None): pass
    def setProperty(self, k, v): self._props[k] = v
    def property(self, k): return self._props.get(k)
    def update(self, *a): pass
    def hasFocus(self): return False
    def setFocus(self, *a): pass
    def font(self): return Dummy()
    # default event handlers (so super() calls work)
    def mousePressEvent(self, e): pass
    def mouseMoveEvent(self, e): pass
    def mouseReleaseEvent(self, e): pass
    def mouseDoubleClickEvent(self, e): pass
    def keyPressEvent(self, e): pass
    def wheelEvent(self, e): pass
    def resizeEvent(self, e): pass
    def paintEvent(self, e): pass
    def dragEnterEvent(self, e): pass
    def dragMoveEvent(self, e): pass
    def dropEvent(self, e): pass
    def closeEvent(self, e): pass


_SIGNAL_NAMES = {"clicked", "toggled", "valueChanged", "triggered", "timeout", "activated", "currentIndexChanged",
                 "textChanged", "sliderMoved", "sliderReleased", "sliderPressed", "idClicked", "itemActivated",
                 "customContextMenuRequested", "itemDoubleClicked", "pressed", "released"}


class QTimer(Base):
    _single_shots = []

    def __init__(self, *a):
        super().__init__()
        self.timeout = BoundSignal(self)
        self.active = False
    def start(self, *a): self.active = True
    def stop(self): self.active = False
    def isActive(self): return self.active
    @staticmethod
    def singleShot(ms, fn):
        QTimer._single_shots.append(fn)


class QPoint:
    def __init__(self, x=0, y=0):
        self._x, self._y = int(x), int(y)
    def x(self): return self._x
    def y(self): return self._y
    def toPoint(self): return QPoint(self._x, self._y)


class QPointF(QPoint):
    def __init__(self, x=0.0, y=0.0):
        self._x, self._y = float(x), float(y)
    def toPoint(self): return QPoint(round(self._x), round(self._y))


class QRect:
    def __init__(self, *a):
        if len(a) == 2 and isinstance(a[0], QPoint):
            self.l, self.t, self.r, self.b = a[0].x(), a[0].y(), a[1].x(), a[1].y()
        elif len(a) == 4:
            self.l, self.t = a[0], a[1]
            self.r, self.b = a[0] + a[2] - 1, a[1] + a[3] - 1
        else:
            self.l = self.t = self.r = self.b = 0
    def setBottomRight(self, p): self.r, self.b = p.x(), p.y()
    def normalized(self):
        q = QRect()
        q.l, q.r = min(self.l, self.r), max(self.l, self.r)
        q.t, q.b = min(self.t, self.b), max(self.t, self.b)
        return q
    def left(self): return self.l
    def right(self): return self.r
    def top(self): return self.t
    def bottom(self): return self.b


class _Value(Base):
    """Slider / spin box / dial."""
    def __init__(self, *a, **k):
        super().__init__()
        self._min, self._max, self._val = 0, 99, 0
        self._down = False
        self.valueChanged = BoundSignal(self)
        self.sliderMoved = BoundSignal(self)
        self.sliderReleased = BoundSignal(self)
        self.sliderPressed = BoundSignal(self)
    def setRange(self, lo, hi):
        self._min, self._max = lo, hi
        self.setValue(self._val)
    def setMinimum(self, v): self.setRange(v, max(v, self._max))
    def setMaximum(self, v): self.setRange(min(v, self._min), v)
    def minimum(self): return self._min
    def maximum(self): return self._max
    def value(self): return self._val
    def setValue(self, v):
        v = max(self._min, min(self._max, v))
        if isinstance(self._min, int) and not isinstance(self, QDoubleSpinBox):
            v = int(v)
        if v != self._val:
            self._val = v
            self.valueChanged.emit(v)
    def setSliderDown(self, d):
        if d and not self._down:
            self._down = True
            self.sliderPressed.emit()
        elif not d and self._down:
            self._down = False
            self.sliderReleased.emit()
    def isSliderDown(self): return self._down


class QAbstractSlider(_Value): pass
class QSlider(_Value): pass
class QDial(_Value): pass
class QSpinBox(_Value): pass
class QScrollBar(_Value): pass


class QDoubleSpinBox(_Value):
    def setDecimals(self, d): pass


class _Check(Base):
    def __init__(self, *a, **k):
        super().__init__()
        self._checkable = False
        self._checked = False
        self._text = a[0] if a and isinstance(a[0], str) else ""
        self.toggled = BoundSignal(self)
        self.clicked = BoundSignal(self)
    def setCheckable(self, c): self._checkable = c
    def isCheckable(self): return self._checkable
    def setChecked(self, c):
        c = bool(c)
        if c != self._checked:
            self._checked = c
            self.toggled.emit(c)
            grp = getattr(self, "_group", None)
            if c and grp is not None:
                grp._exclusive_set(self)
    def isChecked(self): return self._checked
    def click(self):
        if self._checkable:
            self.setChecked(not self._checked)
        self.clicked.emit(self._checked)
    def setText(self, t): self._text = t
    def text(self): return self._text


class QPushButton(_Check): pass
class QToolButton(_Check): pass


class QCheckBox(_Check):
    def __init__(self, *a):
        super().__init__(*a)
        self._checkable = True


class QButtonGroup(Base):
    def __init__(self, *a):
        super().__init__()
        self.buttons = {}
        self.idClicked = BoundSignal(self)
    def addButton(self, b, i):
        self.buttons[i] = b
        b._group = self
        b.clicked.connect(lambda *_: self.idClicked.emit(i))
    def _exclusive_set(self, btn):
        for b in self.buttons.values():
            if b is not btn and b._checked:
                b._checked = False


class QComboBox(Base):
    def __init__(self, *a):
        super().__init__()
        self.items = []
        self._cur = -1
        self.currentIndexChanged = BoundSignal(self)
        self.activated = BoundSignal(self)
    def addItem(self, text, data=None):
        self.items.append((text, data))
        if self._cur < 0:
            self._cur = 0
            self.currentIndexChanged.emit(0)
    def clear(self):
        self.items = []
        if self._cur != -1:
            self._cur = -1
            self.currentIndexChanged.emit(-1)
    def count(self): return len(self.items)
    def itemData(self, i): return self.items[i][1] if 0 <= i < len(self.items) else None
    def itemText(self, i): return self.items[i][0] if 0 <= i < len(self.items) else ""
    def findData(self, d):
        for i, (_, x) in enumerate(self.items):
            if x == d and type(x) is type(d):
                return i
        return -1
    def currentIndex(self): return self._cur
    def currentData(self): return self.itemData(self._cur)
    def setCurrentIndex(self, i):
        if i != self._cur and -1 <= i < len(self.items):
            self._cur = i
            self.currentIndexChanged.emit(i)
    def user_activate(self, i):
        self.setCurrentIndex(i)
        self.activated.emit(i)


class QLabel(Base):
    def __init__(self, text="", *a):
        super().__init__()
        self._text = text
    def setText(self, t): self._text = t
    def text(self): return self._text


class QLineEdit(Base):
    def __init__(self, *a):
        super().__init__()
        self._text = ""
        self.textChanged = BoundSignal(self)
    def setText(self, t):
        self._text = t
        self.textChanged.emit(t)
    def text(self): return self._text


class QTreeWidgetItem:
    def __init__(self, texts=None):
        self.texts = list(texts or [])
        self.data_ = {}
        self.hidden = False
    def setData(self, col, role, v): self.data_[(col, role)] = v
    def data(self, col, role): return self.data_.get((col, role))
    def text(self, c): return self.texts[c] if c < len(self.texts) else ""
    def setText(self, c, t):
        while len(self.texts) <= c:
            self.texts.append("")
        self.texts[c] = t
    def setHidden(self, h): self.hidden = h
    def isHidden(self): return self.hidden
    def flags(self): return 0
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Dummy()


class QTreeWidget(Base):
    def __init__(self, *a):
        super().__init__()
        self.items = []
        self.itemActivated = BoundSignal(self)
        self.itemDoubleClicked = BoundSignal(self)
    def clear(self): self.items = []
    def addTopLevelItem(self, it): self.items.append(it)
    def topLevelItemCount(self): return len(self.items)
    def topLevelItem(self, i): return self.items[i] if 0 <= i < len(self.items) else None
    def indexOfTopLevelItem(self, it): return self.items.index(it) if it in self.items else -1
    def selectedItems(self): return [it for it in self.items if getattr(it, "_sel", False)]
    def currentItem(self): return None
    def itemAt(self, *a): return None


class QAbstractScrollArea(Base):
    def __init__(self, *a):
        super().__init__()
        self._hs = QScrollBar()
        self._vs = QScrollBar()
        self._vp = Base()
        self._vp._w, self._vp._h = 900, 500
    def horizontalScrollBar(self): return self._hs
    def verticalScrollBar(self): return self._vs
    def viewport(self): return self._vp


class QSettings(Base):
    store = {}

    def value(self, key, default=None, type=None):
        v = QSettings.store.get(key, default)
        if type is not None and v is not None and not isinstance(v, type):
            try:
                v = type(v)
            except Exception:
                v = default
        return v
    def setValue(self, key, v): QSettings.store[key] = v


class QMessageBox(Base):
    answer = None

    @staticmethod
    def question(*a, **k):
        return QMessageBox.answer if QMessageBox.answer is not None else _flag("Yes")
    @staticmethod
    def information(*a, **k): return None
    @staticmethod
    def warning(*a, **k): return None
    @staticmethod
    def critical(*a, **k): return None
    @staticmethod
    def about(*a, **k): return None


class QFileDialog(Base):
    @staticmethod
    def getOpenFileNames(*a, **k): return [], ""
    @staticmethod
    def getOpenFileName(*a, **k): return "", ""
    @staticmethod
    def getSaveFileName(*a, **k): return "", ""
    @staticmethod
    def getExistingDirectory(*a, **k): return ""


class QApplication(Base):
    @staticmethod
    def setOverrideCursor(*a): pass
    @staticmethod
    def restoreOverrideCursor(*a): pass
    @staticmethod
    def processEvents(*a): pass


class QByteArray(Base): pass


class Qt(metaclass=_Meta):
    pass


def make_class(name):
    return type(name, (Base,), {})
