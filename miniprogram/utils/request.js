const { BACKEND } = require('../config.js')

const TOKEN_KEY = 'hh_mp_token'

function getToken() { return wx.getStorageSync(TOKEN_KEY) || '' }
function setToken(t) { if (t) wx.setStorageSync(TOKEN_KEY, t); else wx.removeStorageSync(TOKEN_KEY) }

function request(path, method, body) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BACKEND + '/m/api' + path,
      method: method,
      header: {
        'Content-Type': 'application/json',
        ...(getToken() ? { Authorization: 'Bearer ' + getToken() } : {}),
      },
      data: body || undefined,
      success(res) {
        if (res.statusCode === 401) {
          setToken('')
          wx.reLaunch({ url: '/pages/login/login' })
          return reject(new Error('登录已过期'))
        }
        if (res.statusCode !== 200) return reject(new Error('请求失败(' + res.statusCode + ')'))
        resolve(res.data)
      },
      fail() { reject(new Error('网络错误，请检查域名配置')) },
    })
  })
}

function login(username, password) {
  return request('/auth/login', 'POST', { username, password }).then((d) => {
    setToken(d.access_token)
    return d
  })
}

const mp = {
  overview: (scope = 'mine') => request('/mp/overview?scope=' + scope, 'GET'),
  orders: (kind, scope = 'mine', level = '') =>
    request(`/mp/orders?kind=${kind}&scope=${scope}${level ? '&level=' + level : ''}`, 'GET'),
  rewards: (kind, scope = 'mine') => request(`/mp/rewards?kind=${kind}&scope=${scope}`, 'GET'),
  myTasks: () => request('/mp/my-tasks', 'GET'),
  acceptTask: (id) => request('/mp/my-tasks/' + id + '/accept', 'POST', {}),
  submitTask: (id, comment = '') => request('/mp/my-tasks/' + id + '/submit', 'POST', { comment }),
  notices: () => request('/mp/notices', 'GET'),
  notice: (id) => request('/mp/notices/' + id, 'GET'),
  sign: (id) => request(`/mp/notices/${id}/sign`, 'POST'),
  knowledge: (kw = '') => request('/mp/knowledge?keyword=' + encodeURIComponent(kw), 'GET'),
}

module.exports = { login, getToken, mp }
