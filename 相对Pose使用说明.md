# 相对 Pose 使用说明

本版本在 Studio Library 原有 Pose 功能上增加了“相对 Pose”（Relative Pose）模式。它适合将一组控制器的姿势，按角色当前的位置、朝向和缩放，应用到另一个空间位置。

## 与原版 Pose 的区别

原版 Pose 主要记录并回写控制器属性值，例如 `translateX`、`rotateY`、自定义属性等。加载后，控制器通常会回到保存时的绝对属性状态。

相对 Pose 会为每个控制器记录相对于“相对物体（锚点）”的完整 4×4 变换矩阵：

```text
控制器相对矩阵 = 控制器世界矩阵 × 锚点世界矩阵的逆矩阵
```

加载时，系统使用目标角色当前锚点的世界矩阵重建控制器的位置。因此角色即使已移动、旋转或缩放，姿势仍会跟随当前角色空间正确落位。

相对 Pose 不存储，也不会设置用户自定义属性。这样可以避免在只需要调整空间位置时，意外覆盖角色上的动画开关、IK/FK、空间切换或其他自定义数值。

## 配置默认相对物体

1. 打开 Studio Library 顶部的 **Settings**。
2. 在 **Default Relative Object** 中输入锚点名称，例如 `ctrl_c_rootFollow`。
3. 点击 **Save**。

这里只填写控制器名称，不填写 namespace。保存 Pose 时，系统会使用第一个选中控制器的 namespace 自动查找锚点。

例如：

- 设置：`ctrl_c_rootFollow`
- 选中控制器：`charA:ctrl_hand`
- 实际使用的锚点：`charA:ctrl_c_rootFollow`

相对 Pose 文件会记录本次保存所使用的锚点名称。加载到 `charB` 时，系统会自动使用 `charB:ctrl_c_rootFollow`；无需手工修改 Pose 文件。

## 保存相对 Pose

1. 在 Maya 中选中需要保存的控制器。
2. 新建或保存 Pose。
3. 勾选 **Record Relative**。
4. 默认勾选 **Use Default Relative Object**：使用 Settings 中配置的相对物体作为锚点。
5. 保存 Pose。

如果关闭 **Use Default Relative Object**，则使用**最后一个选中物体**作为锚点。此模式适合临时以某个控制器、道具或局部根控制器为基准保存姿势。

## 应用相对 Pose

1. 将目标角色放到需要的位置和朝向。
2. 确认目标角色对应 namespace 下存在配置的相对物体。
3. 选中需要接收 Pose 的控制器。
4. 打开 Pose；如果文件包含相对矩阵，**Relative** 会自动勾选。
5. 在 **Relative** 勾选状态下点击 Apply 或双击加载。

普通/旧 Pose 文件不包含相对矩阵，打开时 **Relative** 会自动取消勾选，仍按照原版方式加载。

## 特殊控制器顺序

在 Relative 模式下，以下控制器会在其他控制器完成后最后应用：

- `ctrl_r_weapon`
- `ctrl_l_weapon`

该规则支持 namespace，例如 `charA:ctrl_r_weapon`。这样可降低武器控制器依赖其他控制器变换时产生的覆盖问题。

## 注意事项与常见问题

### 找不到锚点

若加载时提示无法找到相对物体，请检查：

- Settings 中的 **Default Relative Object** 名称是否正确；
- 目标角色的 namespace 是否正确；
- 目标角色是否确实存在该控制器；
- 保存时是否启用了 **Use Default Relative Object**。如果关闭过，它使用的是最后选中的控制器，而不是 Settings 配置项。

### 选择顺序会影响手动锚点模式

关闭 **Use Default Relative Object** 后，最后选中的物体就是锚点。请先选择要记录的控制器，最后选择锚点控制器。

加载此类 Pose 时，应同时选择与保存 Pose 匹配的控制器，尤其不要遗漏作为锚点的控制器；否则无法建立正确的相对空间。

### 不要依赖 Relative Pose 写入自定义属性

Relative Pose 有意跳过自定义属性。若需要同时保存 IK/FK、空间切换、手指开关或其他用户属性，请使用普通 Pose，或另行保存这些属性。

### Relative 只在完整应用时生效

矩阵相对变换只在 **Blend = 100%**、未启用 **Mirror**、未启用 **Additive** 时应用。使用滑条预览、镜像、叠加或非 100% 混合时，系统沿用原有属性加载逻辑，不应用相对矩阵。

因此推荐先在 100% Relative 模式下完成定位；需要混合、镜像或叠加时，使用普通 Pose 工作流。

### 旧 Pose 的兼容性

旧 Pose 不含相对矩阵，行为与原版一致。新版 Relative Pose 也会在文件中保存锚点名称，因此之后修改 Settings 中的默认名称不会改变已经保存的 Pose 所使用的锚点名称。

### 约束和连接

被约束、被连接、锁定或被动画曲线驱动的控制器，仍可能拒绝写入某些变换属性。此类问题是 Maya 节点连接与锁定造成的，不是相对矩阵计算错误。请先检查控制器是否允许在当前空间中被直接设置。
