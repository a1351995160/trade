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

## 指标参数

显式实例；非开放参数只接受目录默认值

- MA：窗口 1–252 个交易日。
- ROLLING_VOLATILITY：窗口 2–252 个交易日。

## 使用边界

- 配置可解析不等于账户入口已接通。
- 测试通过不等于策略有效。
- 止损在收盘确认，下一交易日尝试成交，不能保证按止损线成交。
- 波动率指标与跨股票波动率排名是两项不同能力。

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

## 维护方式

运行 `python scripts/generate_research_capabilities_v1.py` 更新本文；
CI 运行 `python scripts/generate_research_capabilities_v1.py --check` 检查本文与公共示例预检。
实际任务的能力 fingerprint 仍绑定实现源码和登记数据，本文不冻结某个部署的指纹。
