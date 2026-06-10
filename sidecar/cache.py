import redis.asyncio as redis

MAPPING_TTL = 86400  # 24h


class CacheService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def get_or_create_token(self, user_id: str, entity_type: str, value: str) -> str:
        reverse_key = f"reverse:{user_id}"
        mapping_key = f"mapping:{user_id}"
        counter_key = f"counter:{user_id}:{entity_type}"

        existing = await self.redis.hget(reverse_key, value)
        if existing:
            return existing.decode()

        count = await self.redis.incr(counter_key)
        token = f"[{entity_type}_{count}]"

        await self.redis.hset(mapping_key, token, value)
        await self.redis.hset(reverse_key, value, token)
        await self.redis.expire(mapping_key, MAPPING_TTL)
        await self.redis.expire(reverse_key, MAPPING_TTL)
        await self.redis.expire(counter_key, MAPPING_TTL)
        return token

    async def get_mapping(self, user_id: str) -> dict:
        data = await self.redis.hgetall(f"mapping:{user_id}")
        return {k.decode(): v.decode() for k, v in data.items()}

    async def clear_mapping(self, user_id: str) -> None:
        await self.redis.delete(f"mapping:{user_id}", f"reverse:{user_id}")
