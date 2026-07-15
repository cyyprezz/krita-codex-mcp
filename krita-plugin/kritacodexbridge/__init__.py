from krita import Krita

from .extension import KritaCodexBridgeExtension

Krita.instance().addExtension(KritaCodexBridgeExtension(Krita.instance()))
