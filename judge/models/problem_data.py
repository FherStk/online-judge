import errno
import os
from zipfile import ZipFile

import yaml
from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.utils.problem_data import ProblemDataStorage

__all__ = ['problem_data_storage', 'problem_directory_file', 'ProblemData', 'ProblemTestCase', 'CHECKERS']

problem_data_storage = ProblemDataStorage()


def _problem_directory_file(code, filename):
    return os.path.join(code, os.path.basename(filename))


def problem_directory_file(data, filename):
    return _problem_directory_file(data.problem.code, filename)


CHECKERS = (
    ('standard', _('Standard')),
    ('floats', _('Floats')),
    ('floatsabs', _('Floats (absolute)')),
    ('floatsrel', _('Floats (relative)')),
    ('rstripped', _('Non-trailing spaces')),
    ('sorted', _('Sorted')),
    ('identical', _('Byte identical')),
    ('linecount', _('Line-by-line')),
)


class ProblemData(models.Model):
    problem = models.OneToOneField('Problem', verbose_name=_('problem'), related_name='data_files',
                                   on_delete=models.CASCADE)
    zipfile = models.FileField(verbose_name=_('data zip file'), storage=problem_data_storage, null=True, blank=True,
                               upload_to=problem_directory_file)
    test_cases_override = models.BooleanField(verbose_name=_('overwrite test cases from zip file'), null=True,
                                              blank=True)
    test_cases_content = models.TextField(verbose_name=_('test cases content'), blank=True)
    generator = models.FileField(verbose_name=_('generator file'), storage=problem_data_storage, null=True, blank=True,
                                 upload_to=problem_directory_file)
    output_prefix = models.IntegerField(verbose_name=_('output prefix length'), blank=True, null=True)
    output_limit = models.IntegerField(verbose_name=_('output limit length'), blank=True, null=True)
    feedback = models.TextField(verbose_name=_('init.yml generation feedback'), blank=True)
    checker = models.CharField(max_length=10, verbose_name=_('checker'), choices=CHECKERS, blank=True)
    unicode = models.BooleanField(verbose_name=_('enable unicode'), null=True, blank=True)
    nobigmath = models.BooleanField(verbose_name=_('disable bigInteger / bigDecimal'), null=True, blank=True)
    checker_args = models.TextField(verbose_name=_('checker arguments'), blank=True,
                                    help_text=_('Checker arguments as a JSON object.'))

    __original_zipfile = None

    def __init__(self, *args, **kwargs):
        super(ProblemData, self).__init__(*args, **kwargs)
        self.__original_zipfile = self.zipfile

        if not self.zipfile:
            # Test cases not loaded through the site, but could be manually created within the problems folder
            if self.has_yml():
                yml = problem_data_storage.open('%s/init.yml' % self.problem.code)
                doc = yaml.safe_load(yml)

                # Load same YML data as in site/judge/utils/problem_data.py -> ProblemDataCompiler()
                if doc.get('archive'):
                    self.zipfile = _problem_directory_file(self.problem.code, doc['archive'])

                if doc.get('generator'):
                    self.generator = _problem_directory_file(self.problem.code, doc['generator'])

                if doc.get('pretest_test_cases'):
                    self.pretest_test_cases = doc['pretest_test_cases']

                if doc.get('output_limit_length'):
                    self.output_limit = doc['output_limit_length']

                if doc.get('output_prefix_length'):
                    self.output_prefix = doc['output_prefix_length']

                if doc.get('unicode'):
                    self.unicode = doc['unicode']

                if doc.get('nobigmath'):
                    self.nobigmath = doc['nobigmath']

                if doc.get('checker'):
                    self.checker = doc['checker']

                if doc.get('hints'):
                    for h in doc['hints']:
                        if h == 'unicode':
                            self.unicode = True
                        if h == 'nobigmath':
                            self.nobigmath = True

                if doc.get('pretest_test_cases'):
                    self._load_problem_test_case(doc, 'pretest_test_cases', True)

                if doc.get('test_cases'):
                    self._load_problem_test_case(doc, 'test_cases', False)

    def _load_problem_test_case(self, doc, field, is_pretest):
        for i, test in enumerate(doc[field]):
            ptc = ProblemTestCase()
            ptc.dataset = self.problem
            ptc.is_pretest = is_pretest
            ptc.order = i

            if test.get('type'):
                ptc.type = test['type']

            if test.get('is_pretest'):
                ptc.is_pretest = True

            if test.get('in'):
                ptc.input_file = test['in']

            if test.get('out'):
                ptc.output_file = test['out']

            if test.get('points'):
                ptc.points = test['points']

            if test.get('generator_args'):
                args = []
                for arg in test['generator_args']:
                    args.append(arg)

                ptc.generator_args = '\n'.join(args)

            if test.get('output_prefix_length'):
                ptc.output_prefix = doc['output_prefix_length']

            if test.get('output_limit_length'):
                ptc.output_limit = doc['output_limit_length']

            if test.get('checker'):
                chk = test['checker']
                if isinstance(chk, str):
                    ptc.checker = chk
                else:
                    ptc.checker = chk['name']
                    ptc.checker_args = chk['args']

            ptc.save()

    def save(self, *args, **kwargs):
        if self.zipfile != self.__original_zipfile:
            self.__original_zipfile.delete(save=False)
            self.__original_zipfile = self.zipfile

        return super(ProblemData, self).save(*args, **kwargs)

    def setup_test_cases_content(self):
        self.test_cases_content = ''

        if self.problem.include_test_cases and self.zipfile:
            zip = ZipFile(self.zipfile)

            content = []
            for i, tc in enumerate([x for x in ProblemTestCase.objects.filter(dataset_id=self.problem.pk) if not x.is_pretest]):
                content.append(f'## Sample Input {i+1}')
                content.append('')
                content.append('```')
                content.append(zip.read(tc.input_file).decode('utf-8'))
                content.append('```')
                content.append('')
                content.append(f'## Sample Output {i+1}')
                content.append('')
                content.append('```')
                content.append(zip.read(tc.output_file).decode('utf-8'))
                content.append('```')
                content.append('')

            self.test_cases_content = '\n'.join(content)

    def load_test_cases_from_zip(self, *args, **kwargs):
        # If test cases should be loaded, a clean load will be performed
        for tc in ProblemTestCase.objects.filter(dataset_id=self.problem.pk):
            tc.delete()

        files = sorted(ZipFile(self.zipfile).namelist())
        input = [x for x in files if '.in' in x or 'input' in x]
        output = [x for x in files if '.out' in x or 'output' in x]

        cases = []
        for i in range(len(input)):
            ptc = ProblemTestCase()
            ptc.dataset = self.problem
            ptc.is_pretest = False
            ptc.order = i
            ptc.input_file = input[i]
            ptc.output_file = output[i]
            ptc.points = 0
            cases.append(ptc)

        return cases

    def has_yml(self):
        return problem_data_storage.exists('%s/init.yml' % self.problem.code)

    def _update_code(self, original, new):
        try:
            problem_data_storage.rename(original, new)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        if self.zipfile:
            self.zipfile.name = _problem_directory_file(new, self.zipfile.name)
        if self.generator:
            self.generator.name = _problem_directory_file(new, self.generator.name)
        self.save()
    _update_code.alters_data = True


class ProblemTestCase(models.Model):
    dataset = models.ForeignKey('Problem', verbose_name=_('problem data set'), related_name='cases',
                                on_delete=models.CASCADE)
    order = models.IntegerField(verbose_name=_('case position'))
    type = models.CharField(max_length=1, verbose_name=_('case type'),
                            choices=(('C', _('Normal case')),
                                     ('S', _('Batch start')),
                                     ('E', _('Batch end'))),
                            default='C')
    input_file = models.CharField(max_length=100, verbose_name=_('input file name'), blank=True)
    output_file = models.CharField(max_length=100, verbose_name=_('output file name'), blank=True)
    generator_args = models.TextField(verbose_name=_('generator arguments'), blank=True)
    points = models.IntegerField(verbose_name=_('point value'), blank=True, null=True)
    is_pretest = models.BooleanField(verbose_name=_('case is pretest?'))
    output_prefix = models.IntegerField(verbose_name=_('output prefix length'), blank=True, null=True)
    output_limit = models.IntegerField(verbose_name=_('output limit length'), blank=True, null=True)
    checker = models.CharField(max_length=10, verbose_name=_('checker'), choices=CHECKERS, blank=True)
    checker_args = models.TextField(verbose_name=_('checker arguments'), blank=True,
                                    help_text=_('Checker arguments as a JSON object.'))
