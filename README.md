- 读取C:\Users\Administrator\AppData\LocalLow\laolin\BidKing\Player.log文件获取日志  我已经放了一个复制的在根目录下
- item_prices.csv是游戏所有物品的数据  item_id,name（物品名称）,category_tags（分类）,shape（大小 长*宽）,quality（品质）,base_value（价格）,grid_size
其中一条数据示例如下  1011001,数据线,"[101,107]",11,1,160,"[10,5]"
- 其中category_tags（分类） 由101到110组成
- shape（大小 长*宽）由两位数组成 例如 11 23 12 56等 长宽最大值都为9 最小值为1 整数类型
- quality（品质） 由1-6 数值越大品质越高 整数类型
- base_value（价格） 整数类型


# player.log解析
- S2C_33_game_start_notify为游戏对局开始也就还第一轮的标识其中的对象例子如下
{
    "GameData": {
        "Uid": "对局id",
        "MapId": 地图id,
        "UserLog": [
            {玩家信息},{其他玩家信息}
        ],
        "HeroSkillLog": [
            {玩家技能释放结果}
        ],
        "MapSkillLog": [
            {地图公告技能释放结果}
        ],
        "NextRoundTime": "1777272274",下回合时间戳
        "ServerTime": "1777272214"开始时间戳
    }
}
- S2C_37_game_next_round_notify 为下一回合的标识 结构如下
{
    "GameData": {
        "Uid": "2101:414722387512048",
        "MapId": 2101,
        "Round": 1,初始回合没有这个标识回合数为这个值+1
        "UserLog": [
            {
                "UserUid": "308069414753197",
                "Name": "栎梧",
                "HeroCid": 204,
                "HeadCid": 120000,
                "SelectItemList": [
                    {
                        "ItemCid": 100104
                    },
                    {
                        {
                            "GameData": {
                                "Uid": "2101:414722387512048",
                                "MapId": 2101,
                                "Round": 1,
                                "UserLog": "...",
                                "HeroSkillLog": "...",
                                "MapSkillLog": "...",
                                "NextRoundTime": "1777272287",
                                "ServerTime": "1777272247"
                            }
                        }
                    }
                ]
            },
            {
                其他玩家信息
            }
        ],
        "HeroSkillLog": [
            玩家自动释放的技能结果
        ],
        "MapSkillLog": [
            游戏地图自动释放的技能结果
        ],
        ItemSkillLog:[
            {使用道具触发后的结果}仅显示之前回合的不包含本回合使用的
        ],
        "NextRoundTime": "1777272274",
        "ServerTime": "1777272214"
    }
}
- S2C39GameUseItem : (S2C_39_game_use_item) 为使用道具的标识结构如下
{
    "ItemSkillLog": [
       使用道具触发后的结果
    ]
}

- [Network] OnHanderNotify S2C45GameOverNotify 游戏结束
# 地图技能结果解析
技能id对照 200021随机显示x件物品 200010紫色品质（quality=4）的数量有x个 200027随机显示x件品质 200001随机显示x件quality=4的轮廓与位置
{
    "SkillCid": 200021,技能id
    "MapCid": 2107,地图id
    "CastTime": "1777271928528",
    "HitItemIndex": 2,命中个数
    "HitBoxList": [命中物品列表
        {
            "BoxId": 46,（首格位置 游戏一行10格 从0-9 游戏最多30行 46标识物品首格占位与4行6格 ）
            "ItemUid": "414722386924502",
            "ItemCid": 1031006,
            "ItemSlotType": 21,
            "ItemType": [ 物品所属分类
                103,
                101
            ],
            "ItemQuility": 1,物品品质
            "ItemPrice": 248, 物品价格
            "ItemBoxIndex": 2 所占格数
        },
        {
            ...
        }
    ],
    "AllHitItemAvgPrice": 195, 物品平均价格
    "AllHitBoxAvgPrice": 65, 所占每格平均价格
    "AllHitItemAvgBoxIndex": 3, 每个物品所占平均格数
    "HitItemTotalPrice": 390, 命中物品总价
    "Uid": "414722386924533",
    "TotalHitBoxIndex": 6 总占格子数
}
{
    "SkillCid": 200010,
    "MapCid": 2107,
    "CastTime": "1777271982606",
    "CastRound": 1, 回合
    "Uid": "424342803107571",
    "TotalHitBoxIndex": 12 技能影响数量
}
{
    "SkillCid": 200027,
    "MapCid": 2107,
    "CastTime": "1777272022605",
    "CastRound": 2, 回合
    "HitBoxList": [
        {
            "BoxId": 44,
            "ItemUid": "414722386924498",局内物品id
            "ItemQuility": 1 品质级别
        },
        {
            "BoxId": 47,
            "ItemUid": "414722386924502",
            "ItemQuility": 1
        }
    ],
    "Uid": "424342803115681"
}
{
    "SkillCid": 200001,
    "MapCid": 2107,
    "CastTime": "1777272052606",
    "CastRound": 3,
    "HitBoxList": [
        {
            "BoxId": 27,
            "ItemUid": "414722386924497",
            "ItemSlotType": 22,物品shape（长*宽）
            "ItemQuility": 4 品质级别
        },
        {
            "BoxId": 40,
            "ItemUid": "414722386924499",
            "ItemSlotType": 23, 物品shape（长*宽）
            "ItemQuility": 4  品质级别
        },
    ],
    "Uid": "424342803121896"
}


# 玩家自动释放技能详解  1001034 1001033 1001032 1001031   为艾莎的技能 依次回合顺序进行释放 分别的到Quility1-4的物品大小与品质 以下数据是CastRound=3时HeroSkillLog中的数据
"HeroSkillLog": [
    {
        "SkillCid": 1001034,
        "HeroCid": 103,
        "CastTime": "1777288125663",
        "HitBoxList": [
            {
                "BoxId": 47,
                "ItemUid": "414722425559937",
                "ItemSlotType": 11,
                "ItemQuility": 1
            },
            {
                "BoxId": 15,
                "ItemUid": "414722425559920",
                "ItemSlotType": 11,
                "ItemQuility": 1
            },
        ],
        "Uid": "414722425560586"
    },
    {
        "SkillCid": 1001033,
        "HeroCid": 103,
        "CastTime": "1777288154714",
        "CastRound": 1,
        "HitBoxList": [
            {
                "BoxId": 43,
                "ItemUid": "414722425559935",
                "ItemSlotType": 11,
                "ItemQuility": 2
            },
            {
                "BoxId": 16,
                "ItemUid": "414722425559921",
                "ItemSlotType": 11,
                "ItemQuility": 2
            },
        ],
        "Uid": "424136648577564"
    },
    {
        "SkillCid": 1001032,
        "HeroCid": 103,
        "CastTime": "1777288170713",
        "CastRound": 2,
        "HitBoxList": [
            {
                "BoxId": 24,
                "ItemUid": "414722425559923",
                "ItemSlotType": 33,
                "ItemQuility": 3
            },
            {
                "BoxId": 32,
                "ItemUid": "414722425559927",
                "ItemSlotType": 21,
                "ItemQuility": 3
            }
        ],
        "Uid": "424136648581755"
    },
    {
        "SkillCid": 1001031,
        "HeroCid": 103,
        "CastTime": "1777288192713",
        "CastRound": 3,
        "HitBoxList": [
            {
                "BoxId": 41,
                "ItemUid": "414722425559934",
                "ItemSlotType": 22,
                "ItemQuility": 4
            }
        ],
        "Uid": "424136648587718"
    }
],


# 道具释放技能详解 
"ItemSkillLog": [ 
        {
            "SkillCid": 2001,触发的技能id
            "ItemCid": 100151,使用的道具id
            "CastTime": "1777288838268",
            "CastRound": 1,
            "HitBoxList": [命中的物品
                {
                    "BoxId": 32,
                    "ItemUid": "414722427470146",
                    "ItemSlotType": 11
                }
            ],
            "Uid": "424342807223940"
        }
    ]
道具id,触发技能id,道具名称,鉴别类型,ItemType
100151,2001,【家具物品】鉴影,101
100152,2002,【医疗药品】鉴影,102
100153,2003,【时尚潮流】鉴影,103
100154,2004,【兵装军火】鉴影,104
100155,2005,【珠宝矿藏】鉴影,105
100156,2006,【文物古董】鉴影,106
100157,2007,【数码娱乐】鉴影,107
100158,2008,【能源交通】鉴影,108
100159,2009,【食饮珍馐】鉴影,109
100160,2010,【书画古籍】鉴影,110