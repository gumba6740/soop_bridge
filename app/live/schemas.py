from datetime import datetime

import pydantic as p


class GetChatsResponse(p.BaseModel):
    bid: str
    user_id: str
    user_nickname: str
    message: str


class UserChatRanking(p.BaseModel):
    user_id: str
    chating_count: int


class MinutelyChat(p.BaseModel):
    minutes: datetime
    chating_count: int


class GetStatsResponse(p.BaseModel):
    ranking: list[UserChatRanking]
    minutely: list[MinutelyChat]
