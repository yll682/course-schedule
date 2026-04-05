# 权限系统说明

## 管理员权限 vs 用户组权限

### 两套独立的权限系统

#### 1. 管理员权限 (is_admin)
- **来源**: 环境变量 `ADMIN_USERS`
- **判断**: 用户名是否在 ADMIN_USERS 列表中
- **存储**: session['is_admin'] = True/False
- **特点**:
  - ✅ 拥有**所有**管理员功能权限
  - ✅ 自动拥有 ICS 订阅权限
  - ✅ 自动拥有分享创建权限
  - ✅ 可以访问所有管理 API

#### 2. 用户组权限 (user_groups)
- **来源**: 数据库 user_groups 表
- **字段**: can_use_ics, can_create_share
- **判断**: 查询用户所属组的权限设置
- **特点**:
  - 只对**非管理员**用户生效
  - 可以灵活控制普通用户的权限

---

## 权限判断逻辑

### ICS 订阅权限
```python
def _can_use_ics(username: str, is_admin: bool) -> bool:
    if is_admin:
        return True  # 管理员总是有权限
    # 非管理员检查用户组权限
    group = _get_user_group(username)
    if group:
        return group['can_use_ics']
    return True  # 未分组用户默认有权限
```

### 分享创建权限
```python
def _can_create_share(username: str, is_admin: bool) -> bool:
    if is_admin:
        return True  # 管理员总是有权限
    # 非管理员检查用户组权限
    group = _get_user_group(username)
    if group:
        return group['can_create_share']
    return True  # 未分组用户默认有权限
```

---

## 更新后的权限分配

### 现有用户场景

#### 场景 1: 管理员用户
- **用户**: 2405309121 (在 ADMIN_USERS 中)
- **is_admin**: True
- **group_id**: NULL 或 default
- **最终权限**:
  - ✅ 管理员功能: 有
  - ✅ ICS 订阅: 有 (is_admin=True)
  - ✅ 创建分享: 有 (is_admin=True)

**结论**: 用户组设置**不影响**管理员权限

#### 场景 2: 普通用户（已有账号）
- **用户**: 其他学号 (不在 ADMIN_USERS 中)
- **is_admin**: False
- **group_id**: NULL (自动分配到默认组)
- **默认组权限**: can_use_ics=1, can_create_share=1
- **最终权限**:
  - ❌ 管理员功能: 无
  - ✅ ICS 订阅: 有 (默认组允许)
  - ✅ 创建分享: 有 (默认组允许)

**结论**: 保持原有功能权限，不会获得管理员权限

---

## 默认用户组设置

```sql
-- 默认组 (ID: 1)
name: '默认组'
can_use_ics: 1
can_create_share: 1

-- 受限组 (ID: 2)
name: '受限组'
can_use_ics: 0
can_create_share: 0
```

### 设计原则
- **默认组**: 拥有完整的功能权限，确保向后兼容
- **受限组**: 可以用来限制某些用户的权限

---

## 权限矩阵

| 用户类型 | is_admin | 用户组 | 管理员功能 | ICS订阅 | 创建分享 |
|---------|----------|--------|-----------|---------|----------|
| 管理员 | True | 任意 | ✅ | ✅ | ✅ |
| 普通用户 | False | 默认组 | ❌ | ✅ | ✅ |
| 普通用户 | False | 受限组 | ❌ | ❌ | ❌ |
| 普通用户 | False | NULL | ❌ | ✅ | ✅ |

---

## 关键代码位置

### 管理员判断
```python
# server.py:114
ADMIN_USERS = [u.strip() for u in os.environ.get('ADMIN_USERS', '2405309121').split(',') if u.strip()]

# server.py:492
session['is_admin'] = username in ADMIN_USERS
```

### 权限检查（管理员优先）
```python
# server.py:1246-1255
def _can_use_ics(username: str, is_admin: bool) -> bool:
    if is_admin:
        return True  # 管理员总是返回 True
    # ... 再检查用户组权限
```

---

## 更新安全性验证

### ✅ 管理员不会失去权限
- 管理员权限由 ADMIN_USERS 控制，与用户组无关
- 在权限检查函数中，`is_admin=True` 直接返回 True

### ✅ 普通用户不会获得管理员权限
- 管理员权限由 ADMIN_USERS 列表严格控制
- 用户组只控制功能权限（ICS、分享），不涉及管理员功能

### ✅ 向后兼容
- 默认组拥有完整功能权限
- 现有用户自动分配到默认组
- 不影响任何现有功能

---

## 测试验证

已验证的场景：
- ✅ 管理员登录后 is_admin=True
- ✅ 管理员可访问所有管理 API
- ✅ 普通用户 is_admin=False
- ✅ 普通用户无法访问管理 API
- ✅ 用户组权限只影响功能权限，不影响管理员权限

---

## 结论

**完全安全！不会出现权限混乱：**

1. ✅ 管理员权限由 ADMIN_USERS 环境变量严格控制
2. ✅ 用户组权限不影响管理员权限
3. ✅ 默认组给予完整功能权限，保持向后兼容
4. ✅ 现有用户不会获得或失去管理员权限
5. ✅ 可以通过用户组灵活控制普通用户的 ICS 和分享权限

**设计优势**：
- 管理员权限和功能权限分离
- 灵活的权限管理
- 安全的权限检查
- 完全向后兼容
