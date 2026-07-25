                close_note_modal(page)
                cool_down(cooldown_seconds)
                try:
                    open_note_modal(page, note_id)
                except Exception:
                    # v1.4.1：冷却期间卡片可能已被虚拟滚动回收/Escape 后 DOM 变化，
                    # 重开找不到锚点不算撞墙——放弃本条（明日补抓），绝不上抛炸穿整轮
                    return None, False
                continue