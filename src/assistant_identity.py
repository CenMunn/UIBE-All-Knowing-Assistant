"""当前产品身份；不用于批量改写历史材料。"""
ASSISTANT_NAME='贸小通'
SYSTEM_NAME='贸大百事通助理'


def current_identity_rules():
    return f'当前身份优先：你是“{SYSTEM_NAME}”，昵称“{ASSISTANT_NAME}”。旧版示例中的昵称不作为当前自称。'
