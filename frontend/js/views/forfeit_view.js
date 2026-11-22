export const forfeitViewTemplate = `
<section class="panel">
  <h2>罚没册 · 影子复核</h2>
  <p class="hint">{{ forfeitDoctrine.text || '口径加载中…' }}</p>
  <div class="grid-2">
    <div>
      <div class="form-row">
        <div>
          <label>违约态笼</label>
          <select v-model.number="forfeitForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in failCages" :key="c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }}
            </option>
          </select>
        </div>
        <div>
          <label>金额（空=全额）</label>
          <input type="number" v-model.number="forfeitForm.amount" min="0" step="1000" placeholder="全额"/>
        </div>
        <div style="grid-column:1/-1">
          <label>原因</label>
          <input v-model="forfeitForm.reason"/>
        </div>
      </div>
      <div class="actions">
        <button class="danger" @click="doForfeit">登记影子罚没（待复核）</button>
      </div>
      <h3>待复核影子单 · {{ pendingOrders.length }}</h3>
      <div class="form-row" style="max-width:320px">
        <div>
          <label>复核意见</label>
          <input v-model="reviewNote" placeholder="复核岗签字意见"/>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>凭证</th><th>笼</th><th>金额</th><th>登记人</th><th>时间</th><th>复核</th></tr>
        </thead>
        <tbody>
          <tr v-for="o in pendingOrders" :key="o.id">
            <td>{{ o.order_ref }}</td>
            <td>{{ o.cage_code }}</td>
            <td>{{ fmt(o.amount) }}</td>
            <td>{{ o.created_by }}</td>
            <td>{{ o.created_at }}</td>
            <td>
              <button class="mini-btn" @click="reviewForfeit(o, '通过')">通过晋升</button>
              <button class="mini-btn secondary" @click="reviewForfeit(o, '驳回')">驳回作废</button>
            </td>
          </tr>
          <tr v-if="!pendingOrders.length"><td colspan="6">无待复核影子单</td></tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>正式罚没册（已晋升）</h3>
      <table>
        <thead>
          <tr><th>凭证</th><th>笼</th><th>金额</th><th>原因</th><th>笼代</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in forfeits" :key="f.id">
            <td>{{ f.forfeit_ref }}</td>
            <td>{{ f.cage_code }}</td>
            <td>{{ fmt(f.amount) }}</td>
            <td>{{ f.reason }}</td>
            <td>{{ f.cage_generation }}</td>
            <td>{{ f.created_at }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">影子单历史</h3>
      <table>
        <thead>
          <tr><th>凭证</th><th>笼</th><th>金额</th><th>状态</th><th>复核人</th><th>意见</th></tr>
        </thead>
        <tbody>
          <tr v-for="o in closedOrders" :key="o.id">
            <td>{{ o.order_ref }}</td>
            <td>{{ o.cage_code }}</td>
            <td>{{ fmt(o.amount) }}</td>
            <td><span class="badge" :class="o.status==='已晋升'?'pass':'done'">{{ o.status }}</span></td>
            <td>{{ o.reviewed_by }}</td>
            <td>{{ o.review_note }}</td>
          </tr>
          <tr v-if="!closedOrders.length"><td colspan="6">暂无历史</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
