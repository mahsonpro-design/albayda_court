package org.albayda.court

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { CourtApp() } } }
    }
}

data class CaseItem(val fields: Map<String, String>) {
    val number get() = fields["رقم القضية"].orEmpty()
    val summary get() = listOf("نوع القضية", "المدعي", "المدعى عليه", "برئاسة القاضي", "الجلسة القادمة")
        .mapNotNull { key -> fields[key]?.takeIf(String::isNotBlank)?.let { "$key: $it" } }
        .joinToString("\n")
}

data class UserItem(val id: Int, val name: String, val phone: String, val status: String)

@Composable
private fun CourtApp() {
    val scope = rememberCoroutineScope()
    var token by remember { mutableStateOf<String?>(null) }
    var userName by remember { mutableStateOf("") }
    var role by remember { mutableStateOf("user") }
    var registering by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var query by remember { mutableStateOf("") }
    var cases by remember { mutableStateOf<List<CaseItem>>(emptyList()) }
    var pending by remember { mutableStateOf<List<UserItem>>(emptyList()) }
    var activity by remember { mutableStateOf<List<String>>(emptyList()) }
    var message by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    fun runRequest(block: suspend () -> Unit) {
        if (busy) return
        busy = true
        scope.launch {
            try { withContext(Dispatchers.IO) { block() } }
            catch (e: Exception) { message = e.message ?: "تعذر الاتصال بالخادم." }
            finally { busy = false }
        }
    }

    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("محكمة البيضاء الابتدائية", style = MaterialTheme.typography.headlineSmall)
        Text("نظام الاستعلام الإلكتروني", style = MaterialTheme.typography.titleMedium)
        if (token == null) {
            if (registering) {
                OutlinedTextField(name, { name = it }, label = { Text("الاسم الكامل") }, modifier = Modifier.fillMaxWidth())
            }
            OutlinedTextField(phone, { phone = it }, label = { Text("رقم الجوال") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(password, { password = it }, label = { Text("كلمة المرور") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Button(enabled = !busy, onClick = {
                runRequest {
                    if (registering) {
                        val result = Api.register(name, phone, password)
                        message = result.optString("message", "تم إرسال طلب التسجيل للمسؤول.")
                        registering = false
                    } else {
                        val result = Api.login(phone, password)
                        token = result.getString("access_token")
                        userName = result.getJSONObject("user").optString("name")
                        role = result.getJSONObject("user").optString("role", "user")
                        password = ""
                        message = "مرحباً $userName"
                    }
                }
            }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "جارٍ التنفيذ..." else if (registering) "إرسال طلب التسجيل" else "تسجيل الدخول") }
            TextButton(onClick = { registering = !registering; message = "" }) {
                Text(if (registering) "لديك حساب؟ تسجيل الدخول" else "إنشاء حساب جديد")
            }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("مرحباً، $userName")
                TextButton(onClick = { token = null; cases = emptyList(); pending = emptyList(); activity = emptyList(); message = "تم تسجيل الخروج" }) { Text("خروج") }
            }
            OutlinedTextField(query, { query = it }, label = { Text("رقم القضية أو اسم أحد الأطراف") }, modifier = Modifier.fillMaxWidth())
            Button(enabled = !busy && query.isNotBlank(), onClick = {
                runRequest { cases = Api.search(token!!, query) }
            }, modifier = Modifier.fillMaxWidth()) { Text("بحث في القضايا") }
            if (role == "admin") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = !busy, onClick = { runRequest { pending = Api.pendingUsers(token!!) } }) { Text("طلبات التسجيل") }
                    Button(enabled = !busy, onClick = { runRequest { activity = Api.activity(token!!) } }) { Text("سجل النشاط") }
                }
                LazyColumn(Modifier.weight(1f, fill = false), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(pending, key = { it.id }) { person ->
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text("${person.name} — ${person.phone}")
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(enabled = !busy, onClick = { runRequest { Api.decide(token!!, person.id, true); pending = Api.pendingUsers(token!!) } }) { Text("موافقة") }
                                    TextButton(enabled = !busy, onClick = { runRequest { Api.decide(token!!, person.id, false); pending = Api.pendingUsers(token!!) } }) { Text("رفض") }
                                }
                            }
                        }
                    }
                    items(activity) { Text(it, style = MaterialTheme.typography.bodySmall) }
                }
            }
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(cases) { item ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text("القضية ${item.number}", style = MaterialTheme.typography.titleMedium)
                            Text(item.summary)
                        }
                    }
                }
            }
        }
        if (message.isNotBlank()) Text(message, color = MaterialTheme.colorScheme.primary)
    }
}

private object Api {
    private val client = okhttp3.OkHttpClient.Builder().build()
    private fun base(): String {
        val value = BuildConfig.API_BASE_URL.trim()
        require(value.startsWith("https://")) { "اضبط عنوان API_BASE_URL إلى رابط HTTPS صالح في إعدادات البناء." }
        return value.trimEnd('/')
    }
    private fun request(path: String, method: String = "GET", body: JSONObject? = null, token: String? = null): String {
        val builder = okhttp3.Request.Builder().url(base() + path).header("Accept", "application/json")
        if (token != null) builder.header("Authorization", "Bearer $token")
        if (method == "POST") builder.post((body?.toString() ?: "{}").toRequestBody("application/json; charset=utf-8".toMediaType()))
        val response = client.newCall(builder.method(method, if (method == "GET") null else (body?.toString() ?: "{}").toRequestBody("application/json; charset=utf-8".toMediaType())).build()).execute()
        response.use {
            val text = it.body?.string().orEmpty()
            if (!it.isSuccessful) {
                val detail = try { JSONObject(text).optString("detail", "") } catch (_: Exception) { "" }
                throw IllegalStateException(detail.ifBlank { "فشل الطلب (${it.code})" })
            }
            return text
        }
    }
    fun register(name: String, phone: String, password: String) = JSONObject(request("/api/auth/register", "POST", JSONObject().put("name", name).put("phone", phone).put("password", password)))
    fun login(phone: String, password: String) = JSONObject(request("/api/auth/login", "POST", JSONObject().put("phone", phone).put("password", password)))
    fun search(token: String, query: String): List<CaseItem> {
        val encoded = java.net.URLEncoder.encode(query, "UTF-8")
        val result = JSONObject(request("/api/cases/search?q=$encoded", token = token)).getJSONArray("cases")
        return result.toCaseItems()
    }
    fun pendingUsers(token: String): List<UserItem> {
        val users = JSONObject(request("/api/admin/users?status=pending", token = token)).getJSONArray("users")
        return (0 until users.length()).map { i -> users.getJSONObject(i).toUser() }
    }
    fun decide(token: String, id: Int, approve: Boolean) {
        request("/api/admin/users/$id/decision", "POST", JSONObject().put("approve", approve), token)
    }
    fun activity(token: String): List<String> {
        val events = JSONObject(request("/api/admin/activity?limit=100", token = token)).getJSONArray("events")
        return (0 until events.length()).map { i ->
            val event = events.getJSONObject(i)
            val actor = event.optJSONObject("actor")
            "${event.optString("created_at")} — ${actor?.optString("name") ?: "غير مسجل"} (${actor?.optString("phone") ?: ""}) — ${event.optString("action")}"
        }
    }
}

private fun JSONArray.toCaseItems(): List<CaseItem> = (0 until length()).map { index ->
    val item = getJSONObject(index)
    val fields = mutableMapOf<String, String>()
    item.keys().forEach { key -> fields[key] = item.optString(key) }
    CaseItem(fields)
}

private fun JSONObject.toUser() = UserItem(getInt("id"), optString("name"), optString("phone"), optString("status"))

private fun String.toRequestBody(mediaType: okhttp3.MediaType) = okhttp3.RequestBody.create(mediaType, this)
private fun String.toMediaType() = okhttp3.MediaType.parse(this)!!
