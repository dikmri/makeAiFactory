from ..i18n import tr


class MakeAiFactoryError(Exception):
    pass


class SystemUnsupportedError(MakeAiFactoryError):
    pass


class DriverTooOldError(MakeAiFactoryError):
    pass


class DiskSpaceError(MakeAiFactoryError):
    def __init__(self, required_gb: float, available_gb: float):
        self.required_gb = required_gb
        self.available_gb = available_gb
        super().__init__(tr("空き容量不足: 必要 {required:.1f}GB / 利用可能 {available:.1f}GB").format(
            required=required_gb, available=available_gb))


class DownloadError(MakeAiFactoryError):
    pass


class HashMismatchError(MakeAiFactoryError):
    def __init__(self, path: str, expected: str, actual: str):
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(tr("SHA256不一致: {path}").format(path=path))


class ComfyStartError(MakeAiFactoryError):
    pass


class MissingNodeError(MakeAiFactoryError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(tr("不足custom node class: {missing}").format(missing=", ".join(missing)))


class MissingModelError(MakeAiFactoryError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(tr("不足モデル: {missing}").format(missing=", ".join(missing)))


class PromptValidationError(MakeAiFactoryError):
    pass


class GenerationError(MakeAiFactoryError):
    pass


class OutputNotFoundError(MakeAiFactoryError):
    pass


class JobCancelledError(MakeAiFactoryError):
    """SCH-01 PR3: GenerationExecutor.run の cancel_check が True を返した際に送出される。

    MakeAiFactoryError を継承しているため、呼び出し側 (app.py 等) の既存の
    `except MakeAiFactoryError` 分岐をそのまま使い回せる。Desktop経路が従来
    投げていた `MakeAiFactoryError("生成がキャンセルされました")` と同等に扱われる。
    """
    pass


class SetupError(MakeAiFactoryError):
    pass
