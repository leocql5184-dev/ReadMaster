import io
import json
import unittest
import urllib.error
import ssl
from unittest.mock import patch
from readmaster.agents import ApiAgent, ROLE_PROMPTS, validate_schema
from readmaster.pipeline import run_batch, glossary_schema, translation_schema, review_schema
from readmaster.budgets import budget, negotiate, TARGET, _learned

class ApiChannelTests(unittest.TestCase):
    def setUp(self):
        _learned.clear()
        sleeper = patch('readmaster.agents.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_shared_budget_all_vendors_and_openai_cap(self):
        for host in ('api.deepseek.com', 'api.x.ai', 'generativelanguage.googleapis.com',
                     'open.bigmodel.cn', 'api.moonshot.cn', 'api.moonshot.ai',
                     'ark.cn-beijing.volces.com', 'api.anthropic.com', 'custom.example'):
            self.assertEqual(budget('https://' + host + '/v1', 'example'), ('max_tokens', TARGET))
        self.assertEqual(budget('https://api.openai.com/v1', 'gpt-5'), ('max_completion_tokens',128000))
        self.assertEqual(budget('https://api.openai.com/v1', 'future-model'), ('max_completion_tokens',TARGET))

    def test_explicit_cap_retry_and_reuse(self):
        agent=ApiAgent({'endpoint':'https://custom.example/v1','model':'example'},'dummy-key')
        error=urllib.error.HTTPError('https://custom.example/v1',400,'error',{},io.BytesIO(json.dumps({'error':{'message':'max_tokens must be between 1 and 65536'}}).encode()))
        with patch('readmaster.agents.urllib.request.urlopen',side_effect=[error,self.response({'ok':True})]) as call:
            self.assertEqual(agent.call('probe',{},{}),{'ok':True})
            self.assertEqual(call.call_count,2)
            self.assertEqual(json.loads(call.call_args.args[0].data)['max_tokens'],65536)
        self.assertEqual(budget('https://custom.example/v1','example'),('max_tokens',65536))
        self.assertEqual(budget('https://custom.example/v1','other'),('max_tokens',TARGET))

    def test_parameter_switch_and_context_not_mistaken_for_output(self):
        self.assertEqual(negotiate('https://example/v1','m','max_tokens',TARGET,
                         'max_tokens unsupported; use max_completion_tokens instead'),('max_completion_tokens',TARGET))
        self.assertIsNone(negotiate('https://example/v1','m','max_tokens',TARGET,
                         'maximum context length is 8192; max_tokens cannot exceed 4096'))
        self.assertIsNone(negotiate('https://example/v1','m','max_tokens',TARGET,
                         'max_tokens invalid request 400'))

    def response(self, value, finish='stop'):
        return io.BytesIO(json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(value)}}]}).encode())

    def test_openai_uses_strict_schema(self):
        agent=ApiAgent({'endpoint':'https://api.openai.com/v1','model':'test-model'},'dummy-key')
        with patch('readmaster.agents.urllib.request.urlopen',return_value=self.response({'ok':True})) as call:
            self.assertEqual(agent.call('probe',{}, {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}),{'ok':True})
            request=call.call_args.args[0];body=json.loads(request.data)
            self.assertEqual(request.full_url,'https://api.openai.com/v1/chat/completions')
            self.assertEqual(body['response_format']['type'],'json_schema')
            self.assertTrue(body['response_format']['json_schema']['strict'])
            self.assertEqual(request.get_header('Authorization'),'Bearer dummy-key')

    def test_deepseek_all_agent_roles_and_pipeline(self):
        agent=ApiAgent({'endpoint':'https://api.deepseek.com/v1/','model':'deepseek-flash'},'dummy-key')
        seen=[]
        def fake(request,**kwargs):
            body=json.loads(request.data);system=body['messages'][0]['content']
            role=next(r for r,prompt in ROLE_PROMPTS.items() if system.startswith(prompt))
            seen.append(role)
            self.assertEqual(body['response_format'],{'type':'json_object'})
            self.assertEqual(body['max_tokens'],262144)
            self.assertIn('additionalProperties',system)
            self.assertEqual(request.full_url,'https://api.deepseek.com/v1/chat/completions')
            if role=='terms':value={'terms':[]}
            elif role in ('translate','revise'):value={'translations':[{'id':'s1','text':'这可能有帮助。'}]}
            else:value={'passed':True,'issues':[]}
            return self.response(value)
        with patch('readmaster.agents.urllib.request.urlopen',side_effect=fake):
            validate_schema(agent.call('terms',{},glossary_schema()),glossary_schema())
            result=run_batch(agent,[{'id':'s1','source':'It may help.'}],'context',[],2)
            self.assertEqual(result['status'],'accepted')
            validate_schema(agent.call('revise',{},translation_schema()),translation_schema())
        self.assertEqual(seen,['terms','translate','evaluate','accept','revise'])

    def test_schema_rejects_missing_wrong_and_extra_fields(self):
        for value in ({'passed':'true','issues':[]},{'passed':True},{'passed':True,'issues':[],'extra':1}):
            with self.assertRaises(ValueError):validate_schema(value,review_schema())

    def test_glm_json_mode_preserves_structural_validation_and_roles(self):
        agent = ApiAgent({'endpoint': 'https://open.bigmodel.cn/api/paas/v4',
                          'model': 'glm-test'}, 'dummy-key')
        seen = []
        def respond(request, **kwargs):
            body = json.loads(request.data)
            prompt = body['messages'][0]['content']
            role = next(r for r, text in ROLE_PROMPTS.items() if prompt.startswith(text))
            seen.append(role)
            self.assertEqual(request.full_url, 'https://open.bigmodel.cn/api/paas/v4/chat/completions')
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertIn('additionalProperties', prompt)
            if role == 'terms':
                return self.response({'terms': []})
            if role == 'translate':
                return self.response({'translations': [{'id': 's1', 'text': '可能有帮助。'}]})
            if role == 'evaluate':
                return self.response({'passed': False, 'issues': [{'id': 's1', 'severity': 'blocking',
                    'evidence': 'It may help.', 'reason': '测试修订路径', 'suggestion': '补充主语'}]})
            if role == 'revise':
                return self.response({'translations': [{'id': 's1', 'text': '它可能有帮助。'}]})
            return self.response({'passed': True, 'issues': []})
        # Evaluate once with a blocker, then accept the revised version.
        def after_revision(request, **kwargs):
            prompt = json.loads(request.data)['messages'][0]['content']
            if prompt.startswith(ROLE_PROMPTS['evaluate']) and 'revise' in seen:
                seen.append('evaluate')
                return self.response({'passed': True, 'issues': []})
            return respond(request, **kwargs)
        with patch('readmaster.agents.urllib.request.urlopen', side_effect=after_revision):
            validate_schema(agent.call('terms', {}, glossary_schema()), glossary_schema())
            result = run_batch(agent, [{'id': 's1', 'source': 'It may help.'}], 'context', [], 2)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['translations']['s1'], '它可能有帮助。')
        self.assertEqual(seen, ['terms', 'translate', 'evaluate', 'revise', 'evaluate', 'accept'])

    def test_http_errors_are_specific_and_never_echo_secrets(self):
        agent=ApiAgent({'endpoint':'https://api.openai.com/v1','model':'test-model','retry_attempts':1},'never-echo-this-key')
        cases=[(401,'invalid_api_key','密钥'),(402,'balance','余额'),(404,'model_not_found','模型名称'),(429,'insufficient_quota','额度'),(429,'rate_limit','速率'),(400,'invalid_request','格式'),(503,'busy','繁忙')]
        for status,code,hint in cases:
            error=urllib.error.HTTPError('https://api.example.com',status,'error',{},io.BytesIO(json.dumps({'error':{'code':code,'message':'never-echo-this-key'}}).encode()))
            with patch('readmaster.agents.urllib.request.urlopen',side_effect=error):
                with self.assertRaises(RuntimeError) as caught:agent.call('probe',{}, {})
                self.assertIn(hint,str(caught.exception));self.assertNotIn('never-echo-this-key',str(caught.exception))

    def test_timeout_invalid_and_truncated_output(self):
        agent=ApiAgent({'endpoint':'https://api.deepseek.com','model':'deepseek-flash','retry_attempts':1},'dummy-key')
        for response,hint in [(self.response({'ok':True},'length'),'不完整'),(io.BytesIO(b'not json'),'JSON')]:
            with patch('readmaster.agents.urllib.request.urlopen',return_value=response):
                with self.assertRaises(RuntimeError) as caught:agent.call('probe',{}, {})
                self.assertIn(hint,str(caught.exception))
        with patch('readmaster.agents.urllib.request.urlopen',side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError,'超时'):agent.call('probe',{}, {})

if __name__=='__main__':unittest.main()
