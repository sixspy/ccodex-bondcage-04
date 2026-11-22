export const forfeitViewTemplate = `
<section class="panel">
  <h2>罚没册（乙路径 · 复核后入册）</h2>
  <p class="hint">
    仅核验违约的笼可建罚没意图。意图本身不动笼面与账户冻结；复核通过后才入正式罚没册并扣笼，
    复核驳回则意图作废、正式罚没零增。同笼同代仅可有一条待复核意图。
  </p>
  <div class="grid-2">
    <div>
      <h3>建待复核意图</h3>
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
        <button class="danger" @click="doForfeit">生成待复核意图</button>
      </div>
      <h3 style="margin-top:16px">待复核意图（复核岗办结）</h3>
      <table>
        <thead>
          <tr><th>意图号</th><th>笼</th><th>金额</th><th>代</th><th>提出人</th><th>复核意见</th><th>办结</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in pendingIntents" :key="i.id">
            <td>{{ i.intent_ref }}</td>
            <td>{{ i.cage_code }}</td>
            <td>{{ fmt(i.amount) }}</td>
            <td>{{ i.cage_generation }}</td>
            <td>{{ i.requested_by }}</td>
            <td><input v-model="reviewForm.notes[i.id]" placeholder="复核意见"/></td>
            <td>
              <button class="ok" @click="reviewIntent(i, '通过')">复核通过入册</button>
              <button class="secondary" @click="reviewIntent(i, '驳回')">驳回作废</button>
            </td>
          </tr>
          <tr v-if="!pendingIntents.length">
            <td colspan="7" class="hint">无待复核意图</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>正式罚没册（仅复核通过）</h3>
      <table>
        <thead>
          <tr><th>凭证</th><th>笼</th><th>金额</th><th>原因</th><th>代</th><th>复核人</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in forfeits" :key="f.id">
            <td>{{ f.forfeit_ref }}</td>
            <td>{{ f.cage_code }}</td>
            <td>{{ fmt(f.amount) }}</td>
            <td>{{ f.reason }}</td>
            <td>{{ f.cage_generation }}</td>
            <td>{{ f.reviewed_by }}</td>
            <td>{{ f.created_at }}</td>
          </tr>
          <tr v-if="!forfeits.length">
            <td colspan="7" class="hint">正式罚没册为零</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:16px">已办结意图</h3>
      <table>
        <thead>
          <tr><th>意图号</th><th>笼</th><th>金额</th><th>状态</th><th>复核人</th><th>意见</th><th>正式行</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in doneIntents" :key="i.id">
            <td>{{ i.intent_ref }}</td>
            <td>{{ i.cage_code }}</td>
            <td>{{ fmt(i.amount) }}</td>
            <td>
              <span class="badge" :class="i.status==='已通过'?'pass':'fail'">{{ i.status }}</span>
            </td>
            <td>{{ i.reviewed_by }}</td>
            <td>{{ i.review_note }}</td>
            <td>{{ i.forfeit_id || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
