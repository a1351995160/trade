# 系统能做什么

本表由公共能力查询生成；入口接通与实际验证分别记录。

| 功能 | 底层能力 | 公共入口 | 验证证据 |
|---|---|---|---|
| 指标买卖规则 | 有 | 已接通 | NOT_ACCEPTED |
| 按实际买入成本止损 | 有 | 已接通 | NOT_ACCEPTED |
| 固定比例止盈 | 有 | 已接通 | NOT_ACCEPTED |
| 从持仓最高收盘价回落退出 | 有 | 已接通 | NOT_ACCEPTED |
| 全股票池买入持有基准 | 有 | 已接通 | NOT_ACCEPTED |
| 按ATR波幅退出 | 有 | 未接通 | NOT_ACCEPTED |
| 跨股票按波动率排名选股 | 不支持 | 未接通 | UNSUPPORTED |

## 全范围研究与三个板块

全范围请求由系统扫描登记范围的全部证券，再由策略信号选股；所有证券竞争同一份账户资金。
缓存股票数、历史清单是否完整、信号数据是否齐全、账户是否可运行分别记录。缺口不会缩成少数示例股票。

| 板块 | 股票代码 | 指标与组合 | 账户与退出 | 工程验收 | 真实全范围验收 |
|---|---|---|---|---|---|
| 深圳主板 | 00xxxx.SZ | 同一指标目录、买卖组合规则 | 共享资金、止损/止盈/移动止损 | ENGINEERING_NOT_ACCEPTED | REAL_NOT_ACCEPTED |
| 上海主板 | 60xxxx.SH | 同一指标目录、买卖组合规则 | 共享资金、止损/止盈/移动止损 | ENGINEERING_NOT_ACCEPTED | REAL_NOT_ACCEPTED |
| 创业板 | 30xxxx.SZ | 同一指标目录、买卖组合规则 | 共享资金、止损/止盈/移动止损 | ENGINEERING_NOT_ACCEPTED | REAL_NOT_ACCEPTED |

三板块共同引用 V3 指标目录，共 51 类指标，版本与参数以本目录为准。
历史长度不足或缺少所需字段时显示预热/数据缺口；不能填零、默认无信号或改称已完整扫描。
交易制度按板块与生效日期执行，指标计算能力一致不代表各板块收益相同。

全范围提交使用 `FULL_UNIVERSE_SUBMISSION_V1` 和登记的 `universe_id`，不手填缩小股票名单。
`FULL_UNIVERSE_SUBMISSION_V2` 加 `account_scope=DATA_QUALIFIED`：先检查全池，再冻结全部资料合格股票并公布完整排除清单，最后由策略信号选股。
范围依据数据资格确定，不依据收益、是否成交或信号次数；正常和压力成本共用同一范围。共同来源错误或没有合格股票仍阻断。
补齐后生成新的范围证据，不改旧冻结记录；回顾性资料范围不等于当年完整可投资市场。
公共 `scan` 在既有数据授权内固定规则并检查全目标；prepare、冻结、资格与条件计算同处受限进程（900秒/2048MiB/数值线程1）。
完成资格检查的股票数和实际计算过条件的股票数分别报告；缺来源、当时状态或公司行动证据时保持UNKNOWN，不是零信号。
信号检查不创建账户预算，不计算实际成交或收益；重复同一意图只读复用，已中断意图需对账，不能重开免费扫描。
新版本工程及真实验收独立记录；旧发布包不能覆盖全范围、创业板或新源码。
现金基准与非可投资的期初等权价格对照分开；五万元不能整手买下全池时不改成只买代码靠前几只。
完整使用方法见 [全范围研究说明](FULL_UNIVERSE_RESEARCH.md)。

## 指标参数

显式实例；非开放参数只接受目录默认值

- MA：窗口 1–252 个交易日。
- ROLLING_VOLATILITY：窗口 2–252 个交易日。

## 使用边界

- 配置可解析不等于账户入口已接通。
- 测试通过不等于策略有效。
- 止损在收盘确认，下一交易日尝试成交，不能保证按止损线成交。
- 波动率指标与跨股票波动率排名是两项不同能力。
- market_filter只引用同一股票的价格、成交字段，不代表大盘指数过滤。
- 全范围请求引用登记清单；缓存数量不证明完整历史市场或账户数据合格。

## 验收状态

发布验收单独查询已有凭证；无有效发布凭证时保持未验收，入口可用不代表验收完成。
所有 NOT_ACCEPTED 保留原状态；生成文档和示例预检不会将其改为通过。

## 公共提交示例

以下规则由同一能力目录生成。填入声明式提交的 rule 字段；数据目录、授权、股票范围和资金由实际任务另行冻结。
示例只做公共 preview 预检，不读取行情、不创建账户、不证明有效性。

### ma_cross

```json
{
  "version": "RESEARCH_RULE_STRATEGY_V3",
  "hypothesis": "均线交叉示例，效果尚未验证",
  "change_reason": "公共能力示例",
  "buy": {
    "op": "cross_up",
    "args": [
      {
        "op": "indicator",
        "args": [
          "fast"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      },
      {
        "op": "indicator",
        "args": [
          "slow"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      }
    ],
    "params": {}
  },
  "sell": {
    "op": "cross_down",
    "args": [
      {
        "op": "indicator",
        "args": [
          "fast"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      },
      {
        "op": "indicator",
        "args": [
          "slow"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      }
    ],
    "params": {}
  },
  "market_filter": null,
  "min_hold_sessions": 5,
  "max_hold_sessions": 40,
  "cooldown_sessions": 5,
  "target_weight": 0.5,
  "indicator_instances": [
    {
      "instance_id": "fast",
      "id": "MA",
      "version": "MA_ARITHMETIC_V1",
      "params": {
        "window": 10,
        "price": "close"
      }
    },
    {
      "instance_id": "slow",
      "id": "MA",
      "version": "MA_ARITHMETIC_V1",
      "params": {
        "window": 20,
        "price": "close"
      }
    }
  ],
  "exits": {
    "execution_mode": "CLOSE_CONFIRM_NEXT_SESSION_OPEN",
    "stop_loss_pct": null,
    "take_profit_pct": null,
    "trailing_activate_pct": null,
    "trailing_pct": null
  }
}
```

### ma_cross_with_exits

```json
{
  "version": "RESEARCH_RULE_STRATEGY_V3",
  "hypothesis": "均线交叉与成本退出配置示例，效果尚未验证",
  "change_reason": "公共能力示例",
  "buy": {
    "op": "cross_up",
    "args": [
      {
        "op": "indicator",
        "args": [
          "fast"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      },
      {
        "op": "indicator",
        "args": [
          "slow"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      }
    ],
    "params": {}
  },
  "sell": {
    "op": "cross_down",
    "args": [
      {
        "op": "indicator",
        "args": [
          "fast"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      },
      {
        "op": "indicator",
        "args": [
          "slow"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      }
    ],
    "params": {}
  },
  "market_filter": null,
  "min_hold_sessions": 5,
  "max_hold_sessions": 40,
  "cooldown_sessions": 5,
  "target_weight": 0.5,
  "indicator_instances": [
    {
      "instance_id": "fast",
      "id": "MA",
      "version": "MA_ARITHMETIC_V1",
      "params": {
        "window": 10,
        "price": "close"
      }
    },
    {
      "instance_id": "slow",
      "id": "MA",
      "version": "MA_ARITHMETIC_V1",
      "params": {
        "window": 20,
        "price": "close"
      }
    }
  ],
  "exits": {
    "execution_mode": "CLOSE_CONFIRM_NEXT_SESSION_OPEN",
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.2,
    "trailing_activate_pct": 0.1,
    "trailing_pct": 0.05
  }
}
```

### multi_indicator

```json
{
  "version": "RESEARCH_RULE_STRATEGY_V3",
  "hypothesis": "均线趋势、RSI区间与波动率组合，加同股量能确认的配置示例，效果未验证",
  "change_reason": "公共能力示例",
  "buy": {
    "op": "and",
    "args": [
      {
        "op": "cross_up",
        "args": [
          {
            "op": "indicator",
            "args": [
              "fast"
            ],
            "params": {
              "output": "ma",
              "version": "MA_ARITHMETIC_V1"
            }
          },
          {
            "op": "indicator",
            "args": [
              "slow"
            ],
            "params": {
              "output": "ma",
              "version": "MA_ARITHMETIC_V1"
            }
          }
        ],
        "params": {}
      },
      {
        "op": "between",
        "args": [
          {
            "op": "indicator",
            "args": [
              "rsi"
            ],
            "params": {
              "output": "rsi",
              "version": "RSI_V1"
            }
          },
          {
            "op": "const",
            "args": [],
            "params": {
              "value": 40
            }
          },
          {
            "op": "const",
            "args": [],
            "params": {
              "value": 70
            }
          }
        ],
        "params": {}
      },
      {
        "op": "lt",
        "args": [
          {
            "op": "indicator",
            "args": [
              "volatility"
            ],
            "params": {
              "output": "volatility",
              "version": "ROLLING_VOLATILITY_V1"
            }
          },
          {
            "op": "const",
            "args": [],
            "params": {
              "value": 0.6
            }
          }
        ],
        "params": {}
      }
    ],
    "params": {}
  },
  "sell": {
    "op": "cross_down",
    "args": [
      {
        "op": "indicator",
        "args": [
          "fast"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      },
      {
        "op": "indicator",
        "args": [
          "slow"
        ],
        "params": {
          "output": "ma",
          "version": "MA_ARITHMETIC_V1"
        }
      }
    ],
    "params": {}
  },
  "market_filter": {
    "op": "gt",
    "args": [
      {
        "op": "field",
        "args": [
          "volume"
        ],
        "params": {}
      },
      {
        "op": "ref",
        "args": [
          {
            "op": "field",
            "args": [
              "volume"
            ],
            "params": {}
          }
        ],
        "params": {
          "periods": 1
        }
      }
    ],
    "params": {}
  },
  "min_hold_sessions": 5,
  "max_hold_sessions": 40,
  "cooldown_sessions": 5,
  "target_weight": 0.5,
  "indicator_instances": [
    {
      "instance_id": "fast",
      "id": "MA",
      "version": "MA_ARITHMETIC_V1",
      "params": {
        "window": 10,
        "price": "close"
      }
    },
    {
      "instance_id": "slow",
      "id": "MA",
      "version": "MA_ARITHMETIC_V1",
      "params": {
        "window": 20,
        "price": "close"
      }
    },
    {
      "instance_id": "rsi",
      "id": "RSI",
      "version": "RSI_V1",
      "params": {
        "window": 14,
        "price": "close"
      }
    },
    {
      "instance_id": "volatility",
      "id": "ROLLING_VOLATILITY",
      "version": "ROLLING_VOLATILITY_V1",
      "params": {
        "window": 20,
        "price": "close",
        "annualize": 0,
        "ddof": 1
      }
    }
  ],
  "exits": {
    "execution_mode": "CLOSE_CONFIRM_NEXT_SESSION_OPEN",
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.2,
    "trailing_activate_pct": 0.1,
    "trailing_pct": 0.05
  }
}
```

## 维护方式

运行 `python scripts/generate_research_capabilities_v1.py` 更新本文；
CI 运行 `python scripts/generate_research_capabilities_v1.py --check` 检查本文与公共示例预检。
实际任务的能力 fingerprint 仍绑定实现源码和登记数据，本文不冻结某个部署的指纹。
