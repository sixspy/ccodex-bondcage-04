export const forfeitViewTemplate = `
<section class="panel">
  <h2>罚没册 · 复核台</h2>
  <p class="hint">
    乙路径·复核前笼面零扣（命运说明 2026-11-22 锁死）：提交只落「待复核意图」，
    笼面与账户冻结零改；复核岗（须与提交人分人）通过才原子入正式册并扣笼减冻，
    驳回则意图作废、册与笼面始终零改。任何直进罚没册的写企图记「罚没偏航」并拒写。
  </p>
  <div class="grid-2">
    <div>
      <h3>提交罚没意图</h3>
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
        <button class="danger" @click="doForfeit">提交罚没意图</button>
      </div>
      <p class="hint">提交后资金仍在笼内冻结，等复核办结才定命运。</p>
      <h3 style="margin-top:14px">正式罚没册（只来自复核通过）</h3>
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
    </div>
    <div>
      <h3>待复核队列（{{ pendingIntents.length }}）</h3>
      <table>
        <thead>
          <tr><th>意图凭证</th><th>笼</th><th>金额</th><th>提交人</th><th>复核意见</th><th>办结</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in pendingIntents" :key="i.id">
            <td>{{ i.intent_ref }}</td>
            <td>{{ i.cage_code }}</td>
            <td>{{ fmt(i.amount) }}</td>
            <td>{{ i.officer }}</td>
            <td><input v-model="noteDrafts[i.id]" placeholder="复核意见（可空）"/></td>
            <td>
              <div class="actions">
                <button class="ok" @click="doReview(i, '通过')">通过</button>
                <button class="secondary" @click="doReview(i, '驳回')">驳回</button>
              </div>
            </td>
          </tr>
          <tr v-if="!pendingIntents.length"><td colspan="6">无待复核意图</td></tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">意图办结史</h3>
      <table>
        <thead>
          <tr><th>意图凭证</th><th>笼</th><th>金额</th><th>状态</th><th>复核人</th><th>意见</th><th>正式行</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in settledIntents" :key="i.id">
            <td>{{ i.intent_ref }}</td>
            <td>{{ i.cage_code }}</td>
            <td>{{ fmt(i.amount) }}</td>
            <td>
              <span class="badge" :class="i.status==='已晋升'?'pass':(i.status==='已作废'?'done':'frozen')">{{ i.status }}</span>
            </td>
            <td>{{ i.reviewer }}</td>
            <td>{{ i.review_note }}</td>
            <td>{{ i.forfeit_id || '—' }}</td>
          </tr>
          <tr v-if="!settledIntents.length"><td colspan="7">暂无办结记录</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
