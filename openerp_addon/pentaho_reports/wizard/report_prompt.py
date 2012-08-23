import io
import os
import xmlrpclib
import base64

from lxml import etree

from datetime import datetime

from osv import osv, fields

from tools import config


#---------------------------------------------------------------------------------------------------------------

TYPE_STRING = 'str'
TYPE_BOOLEAN = 'bool'
TYPE_INTEGER = 'int'
TYPE_NUMBER = 'num'
TYPE_DATE = 'date'
TYPE_TIME = 'dtm'


# define mappings as functions, which can be passed the data format to make them conditional...

JAVA_MAPPING = {'java.lang.String' : lambda x: TYPE_STRING,
                'java.lang.Boolean' : lambda x: TYPE_BOOLEAN,
                'java.lang.Number' : lambda x: TYPE_NUMBER,
                'java.util.Date' : lambda x: TYPE_DATE if x and not('H' in x) else TYPE_TIME,
                'java.sql.Date' : lambda x: TYPE_DATE if x and not('H' in x) else TYPE_TIME,
                'java.sql.Time' : lambda x: TYPE_TIME,
                'java.sql.Timestamp' : lambda x: TYPE_TIME,
                'java.lang.Double' : lambda x: TYPE_NUMBER,
                'java.lang.Float' : lambda x: TYPE_NUMBER,
                'java.lang.Integer' : lambda x: TYPE_INTEGER,
                'java.lang.Long' : lambda x: TYPE_INTEGER,
                'java.lang.Short' : lambda x: TYPE_INTEGER,
                'java.math.BigInteger' : lambda x: TYPE_INTEGER,
                'java.math.BigDecimal' : lambda x: TYPE_NUMBER,
                }

MAX_PARAMS = 50  # Do not make this bigger than 999
PARAM_XXX_TYPE = 'param_%03i_type'
PARAM_XXX_REQ = 'param_%03i_req'

PARAM_XXX_STRING_VALUE = 'param_%03i_string_value'
PARAM_XXX_BOOLEAN_VALUE = 'param_%03i_boolean_value'
PARAM_XXX_INTEGER_VALUE = 'param_%03i_integer_value'
PARAM_XXX_NUMBER_VALUE = 'param_%03i_number_value'
PARAM_XXX_DATE_VALUE = 'param_%03i_date_value'
PARAM_XXX_TIME_VALUE = 'param_%03i_time_value'

PARAM_VALUES = {TYPE_STRING : {'value' : PARAM_XXX_STRING_VALUE, 'if_false' : ''},
                TYPE_BOOLEAN : {'value' : PARAM_XXX_BOOLEAN_VALUE, 'if_false' : False},
                TYPE_INTEGER : {'value' : PARAM_XXX_INTEGER_VALUE, 'if_false' : 0},
                TYPE_NUMBER : {'value' : PARAM_XXX_NUMBER_VALUE, 'if_false' : 0.0, 'convert' : lambda x: float(x)},
                TYPE_DATE : {'value' : PARAM_XXX_DATE_VALUE, 'if_false' : '', 'convert' : lambda x: datetime.strptime(x, '%Y-%m-%d'), 'conv_default' : lambda x: datetime.strptime(x.value, '%Y%m%dT%H:%M:%S').strftime('%Y-%m-%d')},
                TYPE_TIME : {'value' : PARAM_XXX_TIME_VALUE, 'if_false' : '', 'convert' : lambda x: datetime.strptime(x, '%Y-%m-%d %H:%M:%S'), 'conv_default' : lambda x: datetime.strptime(x.value, '%Y%m%dT%H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')},
                }


#---------------------------------------------------------------------------------------------------------------


class report_prompt_class(osv.osv_memory):

    _name = "ir.actions.report.promptwizard"

    _columns = {
                'report_name': fields.char('Report Name', size=64, readonly=True),
                'output_type' : fields.selection([('pdf', 'Portable Document (pdf)'),('xls', 'Excel Spreadsheet (xls)'),('csv', 'Comma Separated Values (csv)'),\
                                                  ('rtf', 'Rich Text (rtf)'), ('html', 'HyperText (html)'), ('txt', 'Plain Text (txt)')],\
                                                  'Report format', help='Choose the format for the output', required=True),
                }




    def __init__(self, pool, cr):
        """ Dynamically add columns
        """

        super(report_prompt_class, self).__init__(pool, cr)

#        selections = [map(lambda x: (x(False), ''), set(JAVA_MAPPING.values()))]
        self.longest = reduce(lambda l, x: l and max(l,len(x(False))) or len(x(False)), JAVA_MAPPING.values(), 0)

        for counter in range(0, MAX_PARAMS):
            field_name = PARAM_XXX_TYPE % counter
            self._columns[field_name] = fields.char('Parameter Type', size=self.longest)

            field_name = PARAM_XXX_REQ % counter
            self._columns[field_name] = fields.boolean('Parameter Required')

            field_name = PARAM_XXX_STRING_VALUE % counter
            self._columns[field_name] = fields.char('String Value', size=64)

            field_name = PARAM_XXX_BOOLEAN_VALUE % counter
            self._columns[field_name] = fields.boolean('Boolean Value')

            field_name = PARAM_XXX_INTEGER_VALUE % counter
            self._columns[field_name] = fields.integer('Integer Value')

            field_name = PARAM_XXX_NUMBER_VALUE % counter
            self._columns[field_name] = fields.float('Number Value')

            field_name = PARAM_XXX_DATE_VALUE % counter
            self._columns[field_name] = fields.date('Date Value')

            field_name = PARAM_XXX_TIME_VALUE % counter
            self._columns[field_name] = fields.datetime('Time Value')

        self.paramfile = False




    def _parse_one_report_parameter_default_formula(self, formula, type):

        result = False

        if type == TYPE_DATE:
            if formula == '=NOW()':
                result = datetime.date.today().strftime('%Y-%m-%d')

        if type == TYPE_TIME:
            if formula == '=NOW()':
                result = datetime.date.today().strftime('%Y-%m-%d %H:%M:%S')

        return result




    def _parse_one_report_parameter(self, parameter):
        if not parameter.get('value_type','') in JAVA_MAPPING:
            raise osv.except_osv(('Error'), ("Unhandled parameter type (%s)." % parameter.get('value_type','')))

        if not parameter.get('name', False):
            raise osv.except_osv(('Error'), ("Unnamed parameter encountered."))

        result = {'variable' : parameter['name'], 'label' : parameter['attributes'].get('label','')}

        result['type'] = JAVA_MAPPING[parameter['value_type']](parameter['attributes'].get('data-format', False))

        if parameter.get('default_value',False):
            if PARAM_VALUES[result['type']].get('conv_default', False):
                result['default'] = PARAM_VALUES[result['type']]['conv_default'](parameter['default_value'])
            else:
                result['default'] = parameter['default_value']

        elif parameter['attributes'].get('default-value-formula',False):
            value = self._parse_one_report_parameter_default_formula(parameter['attributes']['default-value-formula'], result['type'])
            if value:
                result['default'] = value

        if parameter.get('is_mandatory',False):
            result['mandatory'] = parameter['is_mandatory']

        return result




    def _parse_report_parameters(self, report_parameters):

        result = []
        for parameter in report_parameters:
            if not parameter.get('attributes',{}):
                raise osv.except_osv(('Error'), ("Parameter received with no attributes."))

            # skip hidden parameters ({'attributes': {'hidden': 'true'}})
            if parameter['attributes'].get('hidden','false') != 'true':
                result.append(self._parse_one_report_parameter(parameter))

        if len(result) > MAX_PARAMS + 1:
            raise osv.except_osv(('Error'), ("Too many report parameters (%d)." % len(self.parameters) + 1))

        return result




    def _setup_parameters(self, cr, uid, context=None):

        if context is None:
            context={}

        ir_actions_obj = self.pool.get('ir.actions.report.xml')
        config_obj = self.pool.get('ir.config_parameter')

        report_ids = ir_actions_obj.search(cr, uid, [('report_name', '=', context.get('service_name',''))], context=context)
        if not report_ids:
            raise osv.except_osv(('Error'), ("Invalid report associated with menu item."))

        report_record = ir_actions_obj.browse(cr, uid, report_ids[0], context=context)

        prpt_content = base64.decodestring(report_record.pentaho_file)

        if not self.paramfile or self.paramfile['report_id'] != report_ids[0] or self.paramfile['prpt_content'] != prpt_content:

            current_user = self.pool.get('res.users').browse(cr, uid, uid)

            proxy = xmlrpclib.ServerProxy(config_obj.get_param(cr, uid, 'pentaho.server.url', default='http://localhost:8090'))
            proxy_argument = {"prpt_file_content": xmlrpclib.Binary(prpt_content),
                              "connection_settings" : {'openerp' : {"host": config["xmlrpc_interface"] or "localhost",
                                                                    "port": str(config["xmlrpc_port"]), 
                                                                    "db": cr.dbname,
                                                                    "login": current_user.login,
                                                                    "password": current_user.password,
                                                                    }},
                              }

            postgresconfig_host = config_obj.get_param(cr, uid, 'postgres.host', default='localhost')
            postgresconfig_port = config_obj.get_param(cr, uid, 'postgres.port', default='5432')
            postgresconfig_login = config_obj.get_param(cr, uid, 'postgres.login')
            postgresconfig_password = config_obj.get_param(cr, uid, 'postgres.password')

            if postgresconfig_host and postgresconfig_port and postgresconfig_login and postgresconfig_password:
                proxy_argument['connection_settings'].update({'postgres' : {'host': postgresconfig_host,
                                                                            'port': postgresconfig_port,
                                                                            'db': cr.dbname,
                                                                            'login': postgresconfig_login,
                                                                            'password': postgresconfig_password,
                                                                            }})

            report_parameters = proxy.report.getParameterInfo(proxy_argument)

            self.parameters = self._parse_report_parameters(report_parameters)

            self.paramfile = {'report_id': report_ids[0], 'prpt_content': prpt_content}




    def default_get(self, cr, uid, fields, context=None):

        self._setup_parameters(cr, uid, context=context)

        defaults = super(report_prompt_class, self).default_get(cr, uid, fields, context=context)

        defaults.update({'report_name': self.pool.get('ir.actions.report.xml').browse(cr, uid, self.paramfile['report_id'], context=context).name,
                         'output_type' : 'pdf',
                         })

        for index in range (0, len(self.parameters)):
            defaults[PARAM_XXX_TYPE % index] = self.parameters[index]['type']
            defaults[PARAM_XXX_REQ % index] = self.parameters[index]['type'] in [TYPE_DATE, TYPE_TIME] or self.parameters[index].get('mandatory', False)

            if self.parameters[index].get('default', False):
                defaults[PARAM_VALUES[self.parameters[index]['type']]['value'] % index] = self.parameters[index]['default']

        return defaults


    def fields_view_get(self, cr, uid, view_id=None, view_type='form', context=None, toolbar=False, submenu=False):

        def add_field(result, field_name):
            result['fields'][field_name] = {'selectable' : self._columns[field_name].selectable,
                                            'type' : self._columns[field_name]._type,
                                            'size' : self._columns[field_name].size,
                                            'string' : self._columns[field_name].string,
                                            'views' : {}
                                            }


        def add_subelement(element, type, **kwargs):
            sf = etree.SubElement(element, type)
            for k, v in kwargs.iteritems():
                sf.set(k, v)


        self._setup_parameters(cr, uid, context=context)

        result = super(report_prompt_class, self).fields_view_get(cr, uid, view_id=view_id, view_type=view_type, context=context, toolbar=toolbar, submenu=submenu)

        doc = etree.fromstring(result['arch'])

        selection_groups = False
        selection_groups = doc.findall('group[@string="selections"]')

        if len(self.parameters) > 0:
            for sel_group in selection_groups:
                add_subelement(sel_group, 'separator',
                               colspan = sel_group.get('col','4'),
                               string = 'Selections',
                               )

        for index in range (0, len(self.parameters)):
            add_field(result, PARAM_XXX_TYPE % index)
            add_field(result, PARAM_XXX_REQ % index)
            add_field(result, PARAM_VALUES[self.parameters[index]['type']]['value'] % index)

            for sel_group in selection_groups:
                add_subelement(sel_group, 'label',
                               string = '%s :' % self.parameters[index]['label'],
                               align = '1.0',
                               colspan = '2',
                               )
                add_subelement(sel_group, 'field',
                               name = PARAM_VALUES[self.parameters[index]['type']]['value'] % index,
                               nolabel = '1',
                               colspan = '2',
                               default_focus = '1' if index==0 else '0',
                               required = '[("%s", "=", True)]' % (PARAM_XXX_REQ % index),
                               )
                add_subelement(sel_group, 'newline')

        for sel_group in selection_groups:
            sel_group.set('string', '')

        result['arch'] = etree.tostring(doc)

        return result




    def _set_report_variables(self, wizard):

        result = {}

        for index in range (0, len(self.parameters)):
            result[self.parameters[index]['variable']] = getattr(wizard, PARAM_VALUES[self.parameters[index]['type']]['value'] % index, False) or PARAM_VALUES[self.parameters[index]['type']]['if_false']

        return result




    def check_report(self, cr, uid, ids, context=None):

        self._setup_parameters(cr, uid, context=context)

        wizard = self.browse(cr, uid, ids[0], context=context)

        if context is None:
            context = {}
        data = {}
        data['ids'] = context.get('active_ids', [])
        data['model'] = context.get('active_model', 'ir.ui.menu')

        data['output_type'] = wizard.output_type

        data['variables'] = self._set_report_variables(wizard)

        # to rely on standard report action, update the action's output
        self.pool.get('ir.actions.report.xml').write(cr, uid, [self.paramfile['report_id']], {'pentaho_report_output_type' : wizard.output_type}, context=context)

        return self._print_report(cr, uid, ids, data, context=context)




    def _print_report(self, cr, uid, ids, data, context=None):

        if context is None:
            context = {}

        return {
            'type': 'ir.actions.report.xml',
            'report_name': context.get('service_name', ''),
            'datas': data,
    }


report_prompt_class()
