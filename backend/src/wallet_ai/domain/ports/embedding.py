"""Puerto del proveedor de embeddings.

`embed` es por lotes: una llamada para todos los textos de una ingesta (o
para la consulta unica de una recuperacion). Se admite tanto un adaptador
asincrono como uno sincrono (el fake): el caso de uso resuelve el awaitable.

`model_name` y `model_version` declaran QUE modelo produjo (o producira) los
vectores. La recuperacion los compara con los registrados en la build antes
de consultar el indice: embeddings de otro modelo no son comparables y el
error debe ser explicito, no silencioso.
"""

from typing import Protocol


class EmbeddingProvider(Protocol):
    model_name: str
    model_version: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
